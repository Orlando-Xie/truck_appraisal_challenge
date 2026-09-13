# What's this truck worth?

A used-truck appraisal from photographs, built for the Kamion Challenge.

Upload photos of a truck. You get a price range in Turkish lira and euro, a
condition report you can check against the pixels, the comparable listings the
number is based on, and a straight answer about what the photos could not tell.

A language model never produces the price. That is the whole point.

## Why this is not a thin wrapper

The brief is explicit: sending photos to a vision API and printing whatever
number comes back will not win. The design here is the opposite of that.

1. **The vision model is only asked what it can see.** Make, generation, rust,
   tread, odometer digits. It is never asked what anything is worth, and it
   cannot invent a range to sound confident. Gemini Flash does a cheap second
   pass on a grille crop and a dashboard crop when those views exist.
2. **The price comes from a statistical model** trained on thousands of real
   Autoline listings. Three gradient-boosted quantile regressors (q10 / q50 /
   q90) are fitted on log asking price in euro. The interval is the observed
   spread of the market at that specification, then calibrated so that about
   80% of held-out asking prices fall inside it.
3. **Condition locates the truck inside that band**, rather than shaving an
   invented amount off a point estimate. Identically specified trucks are
   advertised at very different prices depending on how they were kept; the
   corpus cannot see condition, the photos can. Rubric items that describe
   overall upkeep (rust, body, paint, cab interior) move the truck up or down
   the band. Discrete repair bills (tyres, glass, warning lights, leaks) are
   itemised from `pricing/deductions.yaml` and taken off afterwards.
4. **Refusing is a first-class output.** A night-time blur, a motorcycle, a
   trailer with no tractor, a 60x40 thumbnail: each is rejected with a measured
   reason and a request for the photos that would actually help. A confident
   price on the wrong subject is worse than no price.

## Measured accuracy

Two numbers, kept separate on purpose. Full write-up: `eval/report.md`.

**Spec-oracle** — 119 holdout listings priced from their own year / make / km
(placeholder ads under EUR 5,000 excluded). This is the statistical model when
identification is perfect.

| | |
|---|---|
| Median absolute percentage error | **13.4%** |
| Within 20% of asking price | **62%** |
| Asking price inside the reported interval | **79%** |
| Median interval width | **74%** of the midpoint |

Tractors sit at 12% median error, tippers at 6%. Sparse bodies and missing
power are worse. On the random test split used while fitting (1,020 listings):
median error **14.7%**.

**Photos-only** — 20 holdout listings, photos and nothing else. This is the
challenge as written.

| | |
|---|---|
| Median absolute percentage error | **48.2%** |
| Within 20% of asking price | **25%** |
| Make match rate | **100%** |
| Median \|year error\| | **4 years** |
| Median mileage APE (when both present) | **41%** |

Year and odometer, not make, are the live-demo bottleneck. Asking price is not
transaction price, so a perfect model would still have residual error. The
interval is calibrated against that residual, which is why it is wide, and why
the photos are then used to place a specific truck inside it.

Re-run:

```
python -m eval.holdout
python -m eval.holdout --photos --photos-limit 20
```

## How an appraisal actually runs

```
photos
  |
  |- local quality (OpenCV: blur, exposure, resolution)     free, no API
  |- per-image triage (what is this a photo of?)            concurrent
  |- gates: refuse if unusable / not a truck / details only
  |- identification + condition rubric                      Flash, concurrent
  |- grille / odometer crop pass                            Flash, if those views exist
  |- perceptual-hash match against the corpus
  |- comparable retrieval
  |- market band from the quantile model
  |- widen for what could not be established
  |- position inside the band from visible condition
  `- subtract itemised repair bills, convert to TRY
```

Seller-typed year / make / km are treated as an unverified claim. If the visual
generation disagrees with the typed year, or the interior wear disagrees with
the odometer, both readings are reported and, when it is material, both are
priced.

## Turkish lira

The model is fitted in euro because that is where the data volume is. Conversion
is two explicit factors, both shown in the result:

```
price_TRY = price_EUR x turkiye_multiplier x EURTRY
```

The global multiplier is currently **x1.79** (EURTRY 47.5), fitted from 70
public Turkish asking prices, with make x age-band segments when a slice has
at least three seeds (BMC is lower, Ford F-MAX is higher). Recalibrate with
`python -m pricing.calibrate_turkiye`. It is a market premium, not a fudge:
import duty, OTV, KDV and thinner supply of clean used tractors all push
Turkish asking prices above European ones for the same truck.

arabam.com sits behind Cloudflare. Headless Playwright is blocked; run
`python -m scrape.arabam --headed` and complete the challenge if you want live
Turkish listings. Otherwise `pricing/turkiye_manual.json` is enough.

## Running it

Python 3.11+, Node 20+.

```
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # then put a GEMINI_API_KEY in it
python -m pricing.train --no-remark-holdout
python -m pricing.calibrate_turkiye

cd web && npm install && npm run build && cd ..
python -m uvicorn api.main:app --reload --port 8000
```

Open http://127.0.0.1:8000. Without a vision key the pipeline, the gates, the
pricing and the UI all still run; identification and condition are marked as
synthetic so nobody is misled. Quality-based refusals (dark, blur, tiny) work
without a key. Subject refusals (motorcycle, trailer) need a real vision model.

The one-click cases on the home page cover a modern tractor, a high-mileage
older one, a tipper, a seller whose typed details are wrong, a motorcycle, a
trailer, a dark blur, and a screenshot of an advert.

## Submitting

The challenge wants a repo link and a 4-minute screen recording to
[hack@kamion.co](mailto:hack@kamion.co).

1. Put a Gemini key in `.env` and restart the API, so the live demo identifies
   real trucks rather than the stub.
2. Record four minutes covering: a real tractor appraisal with the arithmetic
   shown, a motorcycle or blur being refused, and one typed-detail contradiction.
3. Push the repo (leave `data/images` and the SQLite file out — they are in
   `.gitignore`; judges can re-scrape, or you can attach a snapshot separately).
4. Send the repo URL and the recording.

## Collecting more data

```
python -m scrape.autoline --all --pages 150
python -m scrape.images --max-per-listing 3 --edge 420 --limit 7500
python -m scrape.arabam --pages 8 --headed
python -m pricing.train --holdout 130
python -m pricing.calibrate_turkiye
```

Be polite: Autoline is scraped at ~1 request/second. Extra European ads barely
move accuracy; Turkish asking prices and sparse body/make slices do.

## Repo layout

```
scrape/       Autoline + arabam scrapers, SQLite, image download, normalisation
pricing/      features, quantile model, deductions.yaml, Turkiye calibration
vision/       schemas, provider client, quality, triage / identify / condition
appraise/     gates, fusion, perceptual-hash dedupe, orchestrator
api/          FastAPI, SSE progress, sample cases
web/          React result card
eval/         holdout evaluation
data/         listings.db, images, samples  (not in git)
```

## What this is not

It is not an inspection. Anything mechanical, structural or documentary needs a
physical check. The number is asking-price guidance from photographs, with the
gaps stated rather than smoothed over.
