import json

from utilities.tm1_connection import get_tm1_connection


TEST_ENDPOINTS = [
    "/rolemgmt/v1/profiles/",
    "/pacontent/v1/Assets",
]


with get_tm1_connection() as tm1:
    rest = tm1._tm1_rest
    session = rest._s
    base_url = rest._base_url

    if not base_url:
        raise RuntimeError("TM1 REST base URL is unavailable.")

    root_url = base_url.split("/tm1/api", 1)[0].rstrip("/")

    print(f"TM1 base URL : {base_url}")
    print(f"PA root URL  : {root_url}")

    for raw_endpoint in TEST_ENDPOINTS:
        endpoint = raw_endpoint.strip()

        if not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"

        url = f"{root_url}{endpoint}"

        try:
            response = session.get(
                url,
                timeout=15,
                allow_redirects=False,
            )

            content_type = response.headers.get("Content-Type", "")
            auth_challenge = response.headers.get("WWW-Authenticate")

            print("\n" + "=" * 100)
            print(f"URL                : {url}")
            print(f"Status             : {response.status_code}")
            print(f"Content-Type       : {content_type}")
            print(f"WWW-Authenticate   : {auth_challenge}")
            print(f"Workspace session  : {response.headers.get('waSessionId')}")
            print(f"Transaction ID     : {response.headers.get('waTransactionId')}")

            if response.status_code == 401:
                print(
                    "RESULT             : Authentication challenge. "
                    "The TM1 session is not authenticated for PAW."
                )
                continue

            if "application/json" in content_type.lower():
                try:
                    payload = response.json()
                    print(json.dumps(payload, indent=2)[:5000])
                except ValueError:
                    print(response.text[:2000])
            else:
                print(response.text[:2000])

        except Exception as ex:
            print("\n" + "=" * 100)
            print(f"URL   : {url}")
            print(f"ERROR : {type(ex).__name__}: {ex}")