# IT knowledge base

## KB-1102: VPN (GlobalProtect) will not connect

1. Confirm you are on a working network: open https://status.contoso.example in a browser.
2. Quit GlobalProtect completely (menu bar icon, Quit), then reopen it.
3. Portal must be `vpn.contoso.example`. If it shows anything else, change it and connect.
4. If you see "certificate expired", your device certificate needs renewing: run
   **Company Portal > Device > Sync**, restart, and try again.
5. Still failing after 15 minutes: open a ticket with category **Network / VPN** and
   attach a screenshot of the error.

Known issue (open since 2026-09-24): users on macOS 26.1 see "gateway unreachable" on the
Houston gateway. Workaround: pick the **US-West** gateway manually.

## KB-1210: Password reset and account lockout

- Self-service reset: https://passwords.contoso.example. You need your registered phone.
- Accounts lock after 5 failed attempts and unlock automatically after 30 minutes.
- New passwords must be 14+ characters and must not reuse the last 12.
- The service desk can never see or set your password; it can only trigger a reset.

## KB-1305: SSO / Okta verify problems

- Lost or replaced phone: call the service desk (x4-4357) to reset MFA. Identity is
  verified with your manager on the call.
- "Push not received": open Okta Verify, pull to refresh, and check notifications are
  allowed.

## KB-1420: Laptop refresh and hardware

- Laptops are refreshed every 4 years. Check eligibility in **ServiceNow > My Assets**.
- A broken laptop under 4 years old is repaired or swapped from depot stock, usually
  next business day in the US, 3 business days elsewhere.
- Approved models: HP EliteBook 860 G11 (standard), HP ZBook Power G11 (engineering).

## KB-1501: Software requests

- Standard catalogue software installs from **Company Portal** with no approval.
- Anything else needs a ServiceNow request with manager approval; licensed software
  also needs a cost center.
