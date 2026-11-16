# 22. The money: decline curves, economics, the invoice check
Who: office
Where: #/economics
Pages: #/decline, #/invoices
Takes: 30 minutes
> What the well cost against the market while it was drilled, what it will produce and what that is worth, and the contractor's invoice day by day against the daily reports.

## Steps
1. Open **Economics**. Declare the day rates with what they pay for and their period, the one-off items (a bit, a cement job) and the AFE, each with its source. Each well's days, footage and hours on bottom come from its own drilling record.
2. **Refresh the EIA prices** fetches the WTI and Henry Hub spot series; **Import a series** keeps any CSV of dates and values with its source. The table gives each well's cost per foot beside the price while it was drilled.
3. Open **Decline curves**. Attach a well's monthly production and declare the terms: the terminal decline, the price basis, the NRI, the severance, the opex, the discount rate.
4. The page fits Arps's curves, forecasts to the economic limit and values it: the EUR and the PV, with the fit drawn over the months it was made from. A forecast is a curve carried forward; the page prints the months it rests on.
5. Open **Invoice check**. Declare the contract's terms once - the hour the report day starts, the day rates, the repair allowance - and attach the daily drilling reports and the invoice as CSV.
6. **Write the report**: each day AGREES, is in QUESTION, or DIFFERS, with the dollars each difference puts in question. It is a list of days to raise with the contractor, each with both documents' lines beside it.

## You know it worked when
The Economics table shows cost per foot beside the WTI price while the well was drilled, and the invoice check names the days that differ.

## The same from a terminal
`gea economics --workspace C:\site`, `gea decline --workspace C:\site`, `gea invoice-check --workspace C:\site`.

Help: economics, decline, invoice-check
