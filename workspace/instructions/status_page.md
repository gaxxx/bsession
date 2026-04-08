# Status Page

## check
1. navigate {data.status.url} -w 5
2. extract overall_status <text.*(?:All Systems|Operational|outage).*>
3. extract incidents <text.*(?:incident|investigating|identified).*>
