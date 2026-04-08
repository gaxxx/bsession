# Flight Price

## search
1. navigate https://www.google.com/travel/flights?q=Flights+from+{data.flight.origin}+to+{data.flight.destination}+on+{data.flight.depart_date} -w 10
2. wait 3
3. extract cheapest_price <text.*\$[\d,]+>
4. extract airline <text.*(?:United|Delta|American|Alaska|JAL|ANA).*>
