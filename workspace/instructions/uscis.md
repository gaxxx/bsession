# USCIS Case Status

## check
1. navigate https://egov.uscis.gov/casestatus/mycasestatus.do -w 8
2. bypass
3. wait 3
4. fill <textbox|text.*receipt|input> {data.uscis.receipt_number}
5. snapshot
6. click <Check Status|button.*Submit|button.*Check> -w 5
7. extract status_title <heading "Case Was.*"|heading "Case Is.*"|heading "Case .*[^Online]">
8. extract status_detail <StaticText "On .*Form I-.*">
