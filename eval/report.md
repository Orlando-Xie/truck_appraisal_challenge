# Holdout evaluation

Generated 2026-09-13T01:21:54.603769+00:00.

The evaluation holdout was excluded from training before the model was fitted.
Two numbers are reported, because they answer different questions.

## Spec-oracle: does the pricing model work?

Each holdout listing is priced from its own year, make, model, mileage, axle
configuration and power -- as if identification were perfect. This is the
accuracy of the statistical model.

- Listings evaluated: **119**
- Median absolute percentage error: **13.4%**
- Within 20% of asking price: **62%**
- True asking price inside the reported interval: **79%**
- Median interval width: **74%** of the midpoint
- Recalibration factor for 80% coverage: **1.05**

### Spec-oracle error slices

By body type:
- `car_transporter`: n=3, median APE 51%
- `refrigerated`: n=10, median APE 32%
- `garbage`: n=4, median APE 23%
- `tow`: n=4, median APE 20%
- `box`: n=25, median APE 13%
- `tractor_unit`: n=51, median APE 12%
- `tipper`: n=22, median APE 6%

By make:
- `Isuzu`: n=1, median APE 149%
- `Mitsubishi`: n=1, median APE 73%
- `Tatra`: n=1, median APE 54%
- `Renault`: n=9, median APE 26%
- `DAF`: n=15, median APE 19%
- `Mercedes-Benz`: n=21, median APE 17%
- `Scania`: n=21, median APE 16%
- `IVECO`: n=15, median APE 13%

Worst misses:
- al-26061609231067148000 Isuzu ELF true EUR 6320 pred 15744 APE 149%
- al-25100916125623842800 IVECO STRALIS true EUR 23270 pred 55537 APE 139%
- al-26091021475541429700 IVECO Daily true EUR 11300 pred 24803 APE 120%
- al-26091018191443612000 IVECO EuroCargo true EUR 12300 pred 24802 APE 102%
- al-21102619164525300300 MAN TGS true EUR 55500 pred 14658 APE 74%
- al-26061609243008419800 Mitsubishi CANTER true EUR 5984 pred 10382 APE 73%

## Photos-only: does the whole system work?

The pipeline sees the listing's photos and nothing else -- no year, make or
mileage. This is the number that answers the challenge as written.

- Listings evaluated: **20**
- Median absolute percentage error: **48.2%**
- Within 20% of asking price: **25%**
- True asking price inside the reported interval: **35%**

- Make match rate: **100%**
- Median |year error|: **4 years**
- Median mileage APE (when both present): **41%**

## What this does and does not mean

Asking price is not transaction price, so a perfect model would still have residual
error: two identical trucks are advertised at different numbers. The interval is
calibrated against that residual, which is why it is wide, and why the condition
photos are then used to place a specific truck inside it rather than to invent a
tighter number from nowhere.
