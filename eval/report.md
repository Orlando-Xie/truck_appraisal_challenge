# Holdout evaluation

Generated 2026-09-12T23:36:54.033048+00:00.

The evaluation holdout was excluded from training before the model was fitted.
Two numbers are reported, because they answer different questions.

## Spec-oracle: does the pricing model work?

Each holdout listing is priced from its own year, make, model, mileage, axle
configuration and power -- as if identification were perfect. This is the
accuracy of the statistical model.

- Listings evaluated: **130**
- Median absolute percentage error: **16.9%**
- Within 20% of asking price: **57%**
- True asking price inside the reported interval: **82%**
- Median interval width: **78%** of the midpoint
- Recalibration factor for 80% coverage: **1.00**

## Photos-only: does the whole system work?

The pipeline sees the listing's photos and nothing else -- no year, make or
mileage. This is the number that answers the challenge as written.

Not run. Configure `GEMINI_API_KEY` and rerun with `--photos`.

## What this does and does not mean

Asking price is not transaction price, so a perfect model would still have residual
error: two identical trucks are advertised at different numbers. The interval is
calibrated against that residual, which is why it is wide, and why the condition
photos are then used to place a specific truck inside it rather than to invent a
tighter number from nowhere.
