# How We Built the Truck Appraisal Model

*A plain-language account of our process, based on the project and saved results as of 13 September 2026.*

## What we wanted to build

Our aim was to help someone estimate the value of a used truck from photographs. The system should provide a price range, describe visible problems, and explain which similar trucks support the estimate. It should also recognize when the photographs do not provide enough information.

We divided the work into two parts. An existing AI vision model reads the photographs and describes the truck. A separate price model, which we trained on vehicle listings, estimates what trucks with those details are advertised for. We did not train the vision model from scratch or ask it to invent a price.

## 1. Collecting the data

The challenge did not provide a dataset, so we collected public vehicle listings. Our main source was Autoline. We built a program to visit listing pages and extract details such as the make, model, year, mileage, engine power, body type, axle arrangement, location, asking price, and photograph links.

We collected several types of trucks, including tractor units, tippers, and box trucks. We also collected examples such as motorcycles, cars, and trailers to help test whether the application could recognize unsuitable inputs. Those examples were excluded from price-model training.

The project database currently contains:

| Collected information | Amount |
|---|---:|
| Listings | 8,519 |
| Listings with a recorded price | 7,182 |
| Downloaded photographs recorded in the database | 21,318 |
| Listings represented by those photographs | 7,179 |

We stored listing details in a local database and photographs in local folders. Keeping the original listing links lets the application show users the advertisements behind its comparisons. Smaller photographs support comparison thumbnails; larger versions were requested for evaluation cases where details matter.

We also built a collector for the Turkish site arabam.com, but automated access was unreliable because of its access checks. The current Turkish price adjustment therefore uses a manually assembled table of 70 Turkish asking prices. This is a limited reference sample, not a large, independently verified Turkish dataset.

One important distinction runs through the whole project: these are **asking prices**, not confirmed sale prices. The model learns what sellers advertise, which may differ from what buyers eventually pay.

## 2. Preparing the listings

The collected listings were not ready to use directly. Sellers describe the same vehicles in different ways, and some advertisements contain incomplete or unusual information.

We standardized make and model names, grouped related models into families and generations, and converted details such as engine power and emissions class into consistent forms. Prices were represented in euros for training, using the listed euro amount where available and fallback conversion rates where necessary.

We then filtered the data. This removed listings outside the project's price, year, and mileage limits, vehicles advertised for parts or as wrecks, and some unusually extreme prices within model families. Listings without usable year or mileage information were excluded from training. Missing details such as engine power received fallback values, with indicators recording that the information was missing.

We turned the remaining details into useful model inputs. For example, we used vehicle age rather than just the manufacturing year, grouped countries into broader market regions, and grouped very rare categories into an “other” category. These steps made the data more consistent and reduced the number of categories supported by only a few examples.

The saved model used 5,665 prepared listings: 4,645 for fitting the model and 1,020 for checking its performance. Separately, the database reserves 130 listings for evaluation; the reported price-model evaluation contains 119 usable cases.

## 3. Building the price model

We used a method called **gradient boosting**. In plain language, it combines many small decision trees. Each tree helps correct errors left by the earlier trees, allowing the model to learn relationships between vehicle details and asking prices.

The inputs include make, model family, generation, age, mileage, body type, axle arrangement, engine power, emissions class, broad advertised condition, and market region. The model learns from combinations of these details. It does not apply one fixed discount for every year of age or every kilometre travelled.

We trained three versions of the model to estimate a lower price, a middle price, and an upper price. Technically, these are the 10th, 50th, and 90th percentiles. The middle estimate represents a typical asking price; the lower and upper estimates describe a wider spread around it.

We trained on a compressed scale of prices, called a logarithmic scale, and converted the results back to euros. This helps the model work across inexpensive older trucks and much more expensive vehicles without focusing only on the largest euro amounts.

We also adjusted the width of the predicted range using the 1,020-listing checking set, aiming for about 80% of asking prices to fall inside the baseline range. Because that set also helped choose the adjustment, its results are not a completely independent test. The separate 119-listing evaluation provides another check.

The trained model and its settings are saved locally. A new appraisal loads that model rather than retraining it.

## 4. Turning photographs into a prediction

An appraisal follows these steps:

1. **Check photograph quality.** The application measures blur, brightness, and resolution. These checks happen locally before the AI analysis.
2. **Identify the subject and views.** The vision model checks whether the photographs show a suitable vehicle and identifies views such as the front, tyres, cab, and dashboard. Unsuitable inputs can be refused.
3. **Read vehicle details and visible condition.** The vision model looks for make, model, generation, axle arrangement, rust, body damage, tyre wear, and other visible evidence. Extra crops of the grille and dashboard help read badges and odometer digits when those views exist.
4. **Assemble the truck's details.** The application combines visual findings with any details supplied by the seller. It flags contradictions. When the year or mileage cannot be read, it may use estimates from the generation, visible wear, or comparable listings and record that uncertainty.
5. **Calculate a baseline price range.** The trained price model estimates a range from the assembled vehicle details. The system retrieves similar listings and can move the estimate toward their median asking price.
6. **Apply condition and uncertainty adjustments.** Missing information widens the range. Visible condition adjusts the estimate within it. Separate repair allowances, such as tyres or glass, are deducted using a written cost table.
7. **Present the result.** The application shows euro and Turkish-lira ranges, condition findings, comparable advertisements, and requests for useful additional photographs.

The condition adjustments and repair allowances are explicit rules, rather than effects learned from a large collection of inspection reports. The condition checks describe what appears visible; they cannot establish the truck's mechanical health from photographs.

For Turkish-lira output, the system applies a Turkish market adjustment and an exchange rate. The market adjustment comes from the Turkish reference prices and can vary by make and age group. These values are stored calibration settings, so the output should not be described as automatically using live market prices or a live exchange rate.

## 5. Measuring accuracy

We tested two different questions: how well the price model works when the truck's details are supplied, and how well the entire application works when it must obtain those details from photographs.

We measured percentage error relative to the advertised price. For example, an estimate of €36,000 against a €30,000 asking price has a 20% error. Median error is the middle result: half the tested cases have a smaller error and half have a larger one.

| Evaluation | Cases priced | Median price error |
|---|---:|---:|
| Model checking set, using listing details | 1,020 | 14.7% |
| Separate evaluation, using listing details | 119 | 13.4% |
| Earlier complete application test, using photographs | 20 | 48.2% |

The photograph test also showed a median year error of four years and a median mileage error of about 41.5%. This suggested that obtaining the right vehicle details was a major weakness. A reasonable price model can still produce a poor estimate if it receives the wrong year, mileage, or model.

These tests use different samples and should not be treated as directly interchangeable. We also found that the older evaluation process could allow evaluation trucks into the comparable pool. Later improvement experiments used a separate database copy with reserved listings and matching make/model/year/mileage records removed from that pool.

## 6. Testing improvements before keeping them

We tested changes in isolation and kept a prediction change only when it passed a confirmation check. We looked for lower median and average error, without reducing either the share of estimates within 20% of asking price or the share of asking prices inside the reported range.

Several ideas did not pass. Replacing wear-based mileage estimates with typical mileage helped the development sample but failed on reserved cases. A retrained price model performed worse than the existing model on the separate specification test. Changing the baseline used for the Turkish adjustment slightly improved median error but worsened average error and interval coverage. Those changes were not retained.

One correction did pass: **typical condition now preserves the model's middle price estimate**. Previously, the condition step could move the estimate to the average of the range's endpoints, even when the truck's condition was typical. For an illustrative €20,000–€60,000 range with a €30,000 model estimate, that would move the estimate to €40,000 without better-condition evidence.

We checked this correction on 24 additional held-out trucks, using Gemini to read their photographs. The system priced 23 and refused one. Both pricing versions received exactly the same saved visual findings.

| Result on the 23 priced trucks | Before | After |
|---|---:|---:|
| Median price error | 63.3% | 57.8% |
| Average price error | 103.3% | 81.4% |
| Estimates within 20% of asking price | 21.7% | 21.7% |
| Asking prices inside the reported range | 17.4% | 21.7% |

We kept this correction, added five regression tests, and replayed the complete application using the saved findings to verify the results. The existing price model was preserved. The new sample is different from the earlier 20-truck sample, so the two photo-test error rates do not measure a before-and-after change by themselves.

## What we learned

The project shows that real listing data can support a useful baseline when the vehicle details are known. It also shows that photograph interpretation, condition rules, and market adjustments can introduce substantial additional error.

Our strongest measured improvement came from correcting how the application used its existing price estimate. More complicated models and seemingly sensible changes did not automatically improve the results.

The current photo-only errors remain high, and the final ranges do not consistently contain asking prices. The application is a prototype that provides an estimate and visible evidence, not a proven replacement for an inspection or a professional valuation. Further work should be judged against independent examples, particularly for year and mileage identification and Turkish market pricing.

### Project records behind this report

- [Saved price-model results](pricing/artifacts/price_model_metrics.json)
- [Original held-out evaluation](pricing/artifacts/holdout_metrics.json)
- [Turkish calibration](pricing/artifacts/turkiye_calibration.json)
- [Detailed improvement experiment and limitations](eval/accuracy_report.md)
- [Results for each truck in the fresh confirmation test](eval/accuracy_results.json)
