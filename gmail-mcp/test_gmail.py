import json

from gmail_client import (
    get_service,
    preview_delete_candidate,
    read_email,
    search_emails,
    trash_email,
)

QUERY = "category:promotions"
MAX_RESULTS = 5
ENABLE_TRASH_TEST = True


def dump(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=True))



def main() -> None:
    print("1. Testing get_service()")
    service = get_service()
    print(f"   Service created: {service is not None}")

    print("\n2. Testing search_emails()")
    results = search_emails(query=QUERY, max_results=MAX_RESULTS)
    print(f"   Found {len(results)} message refs")
    dump(results)

    if not results:
        print("\nNo matching emails found. Skipping read, preview, and trash tests.")
        return

    first_id = results[0]["id"]

    print("\n3. Testing read_email()")
    email_data = read_email(first_id)
    print("   Email preview:")
    dump(email_data)

    print("\n4. Testing preview_delete_candidate()")
    previews = preview_delete_candidate(query=QUERY, max_results=min(3, MAX_RESULTS))
    print(f"   Generated {len(previews)} delete previews")
    dump(previews)

    print("\n5. Testing trash_email()")
    if ENABLE_TRASH_TEST:
        trash_result = trash_email(first_id)
        print("   Trash result:")
        dump(trash_result)
    else:
        print("   Skipped. Set ENABLE_TRASH_TEST = True to move the first matched email to Trash.")


if __name__ == "__main__":
    main()
