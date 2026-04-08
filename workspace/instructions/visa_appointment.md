# US Visa Appointment

## login
1. navigate {data.visa.login_url} -w 8
2. bypass
3. fill <textbox.*email|textbox.*user> {data.visa.username}
4. fill <textbox.*password> {data.visa.password}
5. click <button.*Sign In|button.*Log In|button.*Submit> -w 5

## check
1. navigate {data.visa.schedule_url} -w 8
2. select <combobox|listbox|select.*location> {data.visa.location}
3. wait 3
4. extract available_dates <text.*(?:available|earliest|date).*>
