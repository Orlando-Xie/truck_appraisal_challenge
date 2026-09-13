"""Vision provider abstraction.

Three providers are supported:

* ``gemini``  -- primary, structured output via response_schema.
* ``openai``  -- fallback if a Gemini key is absent or rate limited.
* ``stub``    -- deterministic offline provider. Lets the whole pipeline, the
                 gates, the pricing model and the UI be developed and tested
                 without burning credits or needing a key. It derives its
                 answers from cheap local image statistics, so a blurry dark
                 photo still trips the quality gates.

Model ids are probed against the live model list rather than hardcoded, so a
renamed or retired model degrades to the next candidate instead of breaking the
demo.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel

import config

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class VisionError(RuntimeError):
    pass


def _schema_hint(model_cls: type[BaseModel]) -> str:
    """A compact JSON-schema description for providers without native schemas."""
    schema = model_cls.model_json_schema()
    return json.dumps(schema, ensure_ascii=False)


def _extract_json(text: str) -> Any:
    """Tolerant JSON extraction; models occasionally wrap output in fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


class VisionClient:
    """Async, provider-agnostic structured vision calls."""

    def __init__(self) -> None:
        self.provider = "stub"
        self.flash_model = "stub"
        self.pro_model = "stub"
        self._gemini = None
        self._openai = None
        self._ready = False
        self._lock = asyncio.Lock()

    # -- setup ---------------------------------------------------------------

    async def ensure_ready(self) -> None:
        async with self._lock:
            if self._ready:
                return
            await asyncio.to_thread(self._setup)
            self._ready = True

    def _setup(self) -> None:
        want = config.VISION_PROVIDER
        if want in ("auto", "gemini") and config.GEMINI_API_KEY:
            try:
                self._setup_gemini()
                return
            except Exception as exc:  # pragma: no cover - depends on live API
                log.warning("Gemini setup failed (%s); trying next provider", exc)
        if want in ("auto", "openai") and config.OPENAI_API_KEY:
            try:
                self._setup_openai()
                return
            except Exception as exc:  # pragma: no cover
                log.warning("OpenAI setup failed (%s); falling back to stub", exc)
        self.provider = "stub"
        self.flash_model = self.pro_model = "stub-heuristic"
        log.warning(
            "No vision API key configured. Running with the offline stub provider: "
            "the pipeline, gates and pricing all work but identification is not real."
        )

    def _setup_gemini(self) -> None:
        from google import genai

        self._gemini = genai.Client(api_key=config.GEMINI_API_KEY)
        available: set[str] = set()
        try:
            for m in self._gemini.models.list():
                name = (getattr(m, "name", "") or "").split("/")[-1]
                if name:
                    available.add(name)
        except Exception as exc:  # pragma: no cover
            log.warning("Could not list Gemini models (%s); trusting configured ids", exc)

        def pick(candidates: list[str]) -> str:
            skip = ("thinking", "image", "tts", "audio", "computer-use", "live", "transcribe", "imagen", "veo")
            usable = [n for n in available if not any(s in n.lower() for s in skip)] if available else []
            if not usable:
                usable = list(available)
            for c in candidates:
                if not usable or c in usable:
                    return c
            for name in sorted(usable):
                if "flash" in name and "lite" not in name:
                    return name
            for name in sorted(usable):
                if "flash" in name:
                    return name
            return candidates[0]

        self.flash_model = pick(config.GEMINI_FLASH_CANDIDATES)
        if config.GEMINI_USE_PRO:
            self.pro_model = pick(config.GEMINI_PRO_CANDIDATES)
        else:
            # Same model for both slots: identification and condition run on Flash.
            self.pro_model = self.flash_model
        self.provider = "gemini"
        log.info("Gemini ready: flash=%s pro=%s use_pro=%s", self.flash_model, self.pro_model, config.GEMINI_USE_PRO)

    def _setup_openai(self) -> None:
        from openai import OpenAI

        self._openai = OpenAI(api_key=config.OPENAI_API_KEY)
        available: set[str] = set()
        try:
            available = {m.id for m in self._openai.models.list()}
        except Exception as exc:  # pragma: no cover
            log.warning("Could not list OpenAI models (%s)", exc)
        chosen = config.OPENAI_MODEL_CANDIDATES[0]
        for c in config.OPENAI_MODEL_CANDIDATES:
            if not available or c in available:
                chosen = c
                break
        self.flash_model = self.pro_model = chosen
        self.provider = "openai"
        log.info("OpenAI ready: model=%s", chosen)

    @property
    def is_stub(self) -> bool:
        return self.provider == "stub"

    def describe(self) -> dict:
        return {"provider": self.provider, "flash_model": self.flash_model, "pro_model": self.pro_model}

    # -- main entry point ----------------------------------------------------

    async def structured(
        self,
        *,
        prompt: str,
        images: list[tuple[str, bytes]],
        response_model: type[T],
        use_pro: bool = False,
        temperature: float = 0.1,
        stub_hint: dict | None = None,
    ) -> T:
        """Ask a perceptual question about `images`, get back `response_model`."""
        await self.ensure_ready()
        if self.provider == "stub":
            return _stub_response(response_model, images, stub_hint or {})

        # Pro is gated globally so a leftover use_pro=True in a stage cannot
        # silently spend several times the Flash rate.
        model = self.pro_model if (use_pro and config.GEMINI_USE_PRO) else self.flash_model
        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._call_sync, prompt, images, response_model, model, temperature),
                timeout=config.PER_CALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError as exc:
            raise VisionError(f"vision call timed out after {config.PER_CALL_TIMEOUT_S}s") from exc

        data = _extract_json(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict):
            raise VisionError(f"expected a JSON object, got {type(data).__name__}")
        return response_model.model_validate(data)

    def _call_sync(
        self,
        prompt: str,
        images: list[tuple[str, bytes]],
        response_model: type[BaseModel],
        model: str,
        temperature: float,
    ) -> str:
        if self.provider == "gemini":
            return self._call_gemini(prompt, images, response_model, model, temperature)
        return self._call_openai(prompt, images, response_model, model, temperature)

    def _call_gemini(
        self,
        prompt: str,
        images: list[tuple[str, bytes]],
        response_model: type[BaseModel],
        model: str,
        temperature: float,
    ) -> str:
        from google.genai import types

        parts: list[Any] = []
        for filename, blob in images:
            # The filename is announced before each image so the model can cite
            # which photo a finding came from.
            parts.append(types.Part.from_text(text=f"[image: {filename}]"))
            parts.append(types.Part.from_bytes(data=blob, mime_type=_mime_for(filename)))
        parts.append(types.Part.from_text(text=prompt))

        cfg_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "response_mime_type": "application/json",
            "response_schema": response_model,
            "max_output_tokens": 4096,
        }
        try:
            cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass
        cfg = types.GenerateContentConfig(**cfg_kwargs)
        try:
            resp = self._gemini.models.generate_content(
                model=model, contents=[types.Content(role="user", parts=parts)], config=cfg
            )
        except Exception as exc:
            msg = str(exc)
            if "thinking" in msg.lower() and "thinking_config" in cfg_kwargs:
                cfg_kwargs.pop("thinking_config", None)
                cfg = types.GenerateContentConfig(**cfg_kwargs)
                resp = self._gemini.models.generate_content(
                    model=model, contents=[types.Content(role="user", parts=parts)], config=cfg
                )
            elif "schema" in msg.lower() or "response_schema" in msg.lower():
                log.warning("Structured schema rejected (%s); retrying with prose schema", msg[:160])
                cfg = types.GenerateContentConfig(
                    temperature=temperature, response_mime_type="application/json", max_output_tokens=4096
                )
                parts.append(
                    types.Part.from_text(
                        text="\nReturn JSON conforming exactly to this JSON Schema:\n"
                        + _schema_hint(response_model)
                    )
                )
                resp = self._gemini.models.generate_content(
                    model=model, contents=[types.Content(role="user", parts=parts)], config=cfg
                )
            else:
                raise
        text = getattr(resp, "text", None)
        if not text:
            raise VisionError("empty response from Gemini")
        return text

    def _call_openai(
        self,
        prompt: str,
        images: list[tuple[str, bytes]],
        response_model: type[BaseModel],
        model: str,
        temperature: float,
    ) -> str:
        import base64

        content: list[dict] = []
        for filename, blob in images:
            content.append({"type": "text", "text": f"[image: {filename}]"})
            b64 = base64.b64encode(blob).decode()
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{_mime_for(filename)};base64,{b64}"},
                }
            )
        content.append(
            {
                "type": "text",
                "text": prompt
                + "\n\nReturn JSON conforming exactly to this JSON Schema:\n"
                + _schema_hint(response_model),
            }
        )
        resp = self._openai.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""


def _mime_for(filename: str) -> str:
    low = filename.lower()
    if low.endswith(".png"):
        return "image/png"
    if low.endswith(".webp"):
        return "image/webp"
    if low.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


# ---------------------------------------------------------------------------
# Offline stub
# ---------------------------------------------------------------------------


def _stub_response(model_cls: type[T], images: list[tuple[str, bytes]], hint: dict) -> T:
    """Deterministic pseudo-answers derived from the image bytes.

    Deterministic per image so repeated runs are stable, and driven by the
    locally computed quality metrics in `hint` so the abstention gates behave
    realistically without a key.
    """
    from vision import schemas as S

    seed_src = b"".join(blob[:2048] for _, blob in images) or b"empty"
    seed = int(hashlib.sha256(seed_src).hexdigest()[:8], 16)

    def choice(options: list, salt: int = 0):
        return options[(seed + salt) % len(options)]

    if model_cls is S.BatchTriage:
        items = []
        for fn, blob in images:
            one = _stub_response(S.ImageTriage, [(fn, blob)], hint)
            items.append(S.NamedTriage(filename=fn, **one.model_dump()))
        return model_cls(images=items)  # type: ignore[return-value]

    if model_cls is S.ImageTriage:
        q: dict = hint.get("quality") or {}
        usable = bool(q.get("usable", True))
        return model_cls(  # type: ignore[return-value]
            subject=S.SubjectClass.truck_tractor if usable else S.SubjectClass.indeterminate,
            subject_confidence=0.82 if usable else 0.3,
            view=choice(
                [
                    S.ViewType.front_three_quarter,
                    S.ViewType.side,
                    S.ViewType.tire_wheel,
                    S.ViewType.interior_cab,
                    S.ViewType.dashboard_odometer,
                ]
            ),
            view_confidence=0.7 if usable else 0.25,
            subject_description="[stub provider] no vision model configured",
            vehicle_fingerprint=f"stub-{seed % 1000}",
            usable_for_appraisal=usable,
            obstructions=[] if usable else ["image failed local quality checks"],
        )

    if model_cls is S.Identification:
        make, family, variant, gen, lo, hi = choice(
            [
                ("Mercedes-Benz", "Actros", "1845", "Actros MP4", 2012, 2018),
                ("MAN", "TGX", "18.480", "TGX EURO6", 2014, 2020),
                ("Scania", "R-series", "R450", "R-series Streamline", 2013, 2017),
                ("DAF", "XF", "480", "XF Euro 6", 2014, 2021),
                ("Volvo", "FH", "500", "FH4", 2013, 2020),
            ]
        )
        return model_cls(  # type: ignore[return-value]
            make=make,
            make_confidence=0.8,
            model_family=family,
            model_confidence=0.72,
            model_variant=variant,
            generation=gen,
            generation_year_low=lo,
            generation_year_high=hi,
            year_evidence="[stub provider] synthetic identification, not a real reading of the photos",
            cab_type="high sleeper",
            axle_configuration=choice(["4x2", "6x2"], 3),
            estimated_power_hp=int(variant[-3:]) if variant[-3:].isdigit() else 450,
            euro_class="Euro 6",
            body_type="tractor_unit",
            color=choice(["white", "silver", "blue", "red"], 7),
            identifying_evidence=["[stub provider] no real evidence available"],
            same_vehicle_in_all_photos=True,
            distinct_vehicle_count=1,
        )

    if model_cls is S.ConditionReport:
        sevs = [S.Severity.none, S.Severity.minor, S.Severity.moderate, S.Severity.not_observable]
        findings = [
            S.ConditionFinding(
                item_id=item["id"],
                severity=sevs[(seed + i * 7) % len(sevs)],
                confidence=0.6,
                observation="[stub provider] synthetic finding",
            )
            for i, item in enumerate(config.rubric_items())
        ]
        km_guess = 400_000 + (seed % 600_000)
        return model_cls(  # type: ignore[return-value]
            findings=findings,
            tires=S.TireAssessment(
                tires_assessable=True,
                tires_visible_count=4,
                tread_remaining_pct=20 + (seed % 70),
                uneven_wear=bool(seed % 3 == 0),
                confidence=0.55,
                notes="[stub provider] synthetic tyre assessment",
            ),
            wear=S.WearEstimate(
                odometer_visible=bool(seed % 2),
                odometer_reading_km=km_guess if seed % 2 else 0,
                odometer_confidence=0.6 if seed % 2 else 0.0,
                wear_implied_km_low=int(km_guess * 0.8),
                wear_implied_km_high=int(km_guess * 1.4),
                wear_evidence=["[stub provider] synthetic wear reading"],
                wear_confidence=0.5,
            ),
            overall_impression="[stub provider] no vision model configured, so this report is synthetic.",
            not_observable=["everything -- the stub provider cannot actually see the photos"],
        )

    # Unknown schema: return whatever the defaults allow.
    return model_cls()  # type: ignore[call-arg]


_client: VisionClient | None = None


def get_client() -> VisionClient:
    global _client
    if _client is None:
        _client = VisionClient()
    return _client
