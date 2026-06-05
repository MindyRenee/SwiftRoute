"""Standalone script to create a Stripe webhook endpoint for SAP.

Usage:
    python -m scripts.setup_stripe_webhook \
        --url https://your-domain.com/webhook/stripe \
        --secret-key sk_test_... \
        [--connect] \
        [--description "SAP production webhook"]

The script prints the webhook endpoint ID and signing secret.
Add the secret to your .env as SAP_STRIPE_WEBHOOK_SECRET=whsec_...
"""

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Stripe webhook endpoint for SAP")
    parser.add_argument("--url", required=True, help="HTTPS URL of your /webhook/stripe endpoint")
    parser.add_argument("--secret-key", required=True, help="Stripe secret key (sk_...)")
    parser.add_argument("--connect", action="store_true", help="Receive events from connected accounts")
    parser.add_argument("--description", default="SAP webhook endpoint", help="Webhook description")
    args = parser.parse_args()

    try:
        import stripe
    except ImportError:
        print("Error: stripe package not installed. Run: python -m pip install stripe", file=sys.stderr)
        sys.exit(1)

    stripe.api_key = args.secret_key

    enabled_events = [
        "charge.succeeded",
        "charge.failed",
        "charge.refunded",
        "charge.dispute.created",
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payout.created",
        "payout.paid",
        "payout.failed",
        "transfer.created",
        "transfer.reversed",
    ]

    params: dict = {
        "enabled_events": enabled_events,
        "url": args.url,
        "description": args.description,
    }
    if args.connect:
        params["connect"] = True

    try:
        endpoint = stripe.WebhookEndpoint.create(**params)
    except Exception as exc:
        print(f"Stripe API error: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Webhook endpoint created successfully!")
    print(f"  ID:       {endpoint.id}")
    print(f"  URL:      {endpoint.url}")
    print(f"  Status:   {endpoint.status}")
    print(f"  Secret:   {endpoint.secret}")
    print()
    print("Add this to your .env file:")
    print(f"  SAP_STRIPE_WEBHOOK_SECRET={endpoint.secret}")


if __name__ == "__main__":
    main()
