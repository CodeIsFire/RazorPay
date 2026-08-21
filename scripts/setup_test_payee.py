"""One-time helper: creates ONE real RazorpayX test-mode Contact + Fund
Account for demo purposes, and writes the resulting fund_account_id into
data/fund_account_map.json under a key you choose (should match a ledger
external_ref from the fixtures, e.g. "LED-0018" -- one of the
failed_payment cases -- so a real /pipeline/route retry has somewhere to
send money).

Needs RAZORPAYX_KEY_ID / RAZORPAYX_KEY_SECRET in .env. This sandbox has no
route to api.razorpay.com (see M6 notes) -- run this on a machine with
real internet access, e.g. your own laptop.

Usage:
  python -m scripts.setup_test_payee --ledger-ref LED-0018 \\
      --name "Acme Traders" --ifsc HDFC0000053 --account-number 765432123456789

IFSC/account number can be anything well-formed in test mode -- RazorpayX
doesn't validate against a real bank in test mode, it just needs to look
like a real account number/IFSC.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, razorpayx_client as rzpx  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-ref", required=True,
                         help="the fixture ledger external_ref this payee represents, e.g. LED-0018")
    parser.add_argument("--name", required=True, help="account holder / contact name")
    parser.add_argument("--ifsc", required=True)
    parser.add_argument("--account-number", required=True)
    args = parser.parse_args()

    if not (config.RAZORPAYX_KEY_ID and config.RAZORPAYX_KEY_SECRET):
        print("RAZORPAYX_KEY_ID / RAZORPAYX_KEY_SECRET not set in .env -- nothing to do.")
        raise SystemExit(1)

    print(f"Creating contact '{args.name}'...")
    contact = rzpx.create_contact(name=args.name, contact_type="vendor",
                                   reference_id=args.ledger_ref)
    print(f"  contact_id = {contact['id']}")

    print("Creating fund account...")
    fund_account = rzpx.create_fund_account_bank(
        contact_id=contact["id"], account_holder_name=args.name,
        ifsc=args.ifsc, account_number=args.account_number,
    )
    print(f"  fund_account_id = {fund_account['id']}")

    map_path = Path(config.RAZORPAYX_FUND_ACCOUNT_MAP_PATH)
    existing = json.loads(map_path.read_text()) if map_path.exists() else {}
    existing[args.ledger_ref] = fund_account["id"]
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(existing, indent=2))
    print(f"Wrote {args.ledger_ref} -> {fund_account['id']} to {map_path}")
    print("Also set RAZORPAYX_ACCOUNT_NUMBER in .env (the account payouts debit FROM, "
          "not the fund account above) before /pipeline/route will use the live executor.")


if __name__ == "__main__":
    main()
