# Amazon Product Price

## check
1. navigate {data.amazon.product_url} -w 8
2. bypass
3. extract product_title <heading "([^"]*)">
4. extract price <text.*\$[\d,.]+>
5. extract availability <text.*(?:In Stock|Currently unavailable|stock).*>
