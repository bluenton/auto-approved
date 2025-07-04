# auto_approver.py

import firebase_admin
from firebase_admin import credentials, db
import time
from datetime import datetime, timedelta
import re
import os

# --- Firebase Initialization ---
# IMPORTANT:
# This script expects the 'serviceAccountKey.json' file to be created
# by the GitHub Actions workflow from your GitHub Secret.
# DO NOT commit your actual serviceAccountKey.json file to your repository!
SERVICE_ACCOUNT_KEY_PATH = "serviceAccountKey.json"

if not os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
    print(f"Error: Service account key file not found at '{SERVICE_ACCOUNT_KEY_PATH}'.")
    print("This script is designed to run in a GitHub Actions environment where the key is generated from a secret.")
    print("If running locally, ensure 'serviceAccountKey.json' is in the same directory.")
    exit()

try:
    cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://wetrillion-default-rtdb.firebaseio.com' # Your Firebase Realtime Database URL
    })
    print("Firebase Admin SDK initialized successfully.")
except Exception as e:
    print(f"Error initializing Firebase Admin SDK: {e}")
    print("Please ensure the service account key is valid and the database URL is correct.")
    exit()

# Get database references
transactions_ref = db.reference('transactions')
users_ref = db.reference('users')

def is_valid_utr(utr):
    """Checks if the UTR is exactly 12 digits."""
    # Ensure UTR is treated as a string for regex matching
    return bool(re.fullmatch(r'\d{12}', str(utr)))

def approve_transaction_logic(user_id, transaction_key, transaction_data):
    """
    Approves a single transaction and updates the user's balance.
    This function is called after the 2-minute delay and UTR check.
    """
    try:
        amount = transaction_data.get('amount')
        status = transaction_data.get('status')
        utr = transaction_data.get('transactionId')

        # Double-check status to prevent race conditions if status changed externally
        if status != 'Pending':
            print(f"Transaction {transaction_key} for user {user_id} is no longer pending. Skipping approval.")
            return

        if not isinstance(amount, (int, float)) or amount < 0:
            print(f"Invalid or negative amount for transaction {transaction_key}: {amount}. Skipping approval.")
            return

        # Fetch current user balance
        user_snapshot = users_ref.child(user_id).get()
        current_balance = user_snapshot.get('balance', 0) if user_snapshot else 0
        updated_balance = current_balance + amount

        # Prepare updates for both transaction and user balance
        updates = {
            f'transactions/{user_id}/{transaction_key}/status': 'Approved',
            f'users/{user_id}/balance': updated_balance
        }

        # Atomically update both paths
        db.reference('/').update(updates)
        print(f"Successfully approved transaction {transaction_key} (UTR: {utr}) for user {user_id}. New balance: {updated_balance}")

    except Exception as e:
        print(f"Error approving transaction {transaction_key} for user {user_id}: {e}")

def process_pending_transactions():
    """
    Fetches all pending transactions, checks their age, and approves them
    if they are older than 2 minutes and have a 12-digit UTR.
    """
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking for pending transactions...")
    all_transactions = transactions_ref.get()

    if not all_transactions:
        print("No transactions found in the database.")
        return

    # Iterate through users and their transactions
    for user_id, user_transactions in all_transactions.items():
        # Ensure user_transactions is a dictionary before iterating
        if not isinstance(user_transactions, dict):
            print(f"Skipping malformed user data for user ID: {user_id}")
            continue

        for transaction_key, transaction_data in user_transactions.items():
            # Ensure transaction_data is a dictionary
            if not isinstance(transaction_data, dict):
                print(f"Skipping malformed transaction data for key: {transaction_key} under user: {user_id}")
                continue

            status = transaction_data.get('status')
            utr = transaction_data.get('transactionId')
            date_str = transaction_data.get('date') # Date is expected to be an ISO 8601 string

            if status == 'Pending' and utr and date_str:
                # 1. Validate UTR format
                if not is_valid_utr(utr):
                    print(f"Skipping transaction {transaction_key} (user: {user_id}) due to invalid UTR format: '{utr}' (not 12 digits).")
                    continue

                try:
                    # 2. Parse the date string. Assuming ISO 8601 format (e.g., '2025-07-04T18:00:00.000Z')
                    # .replace('Z', '+00:00') handles the 'Z' (Zulu time) suffix for fromisoformat
                    transaction_time = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    
                    # Ensure current time is timezone-aware if transaction_time is
                    # For simplicity, if transaction_time is UTC, compare with current UTC time
                    current_time_utc = datetime.utcnow().replace(tzinfo=transaction_time.tzinfo)
                    
                    time_difference = current_time_utc - transaction_time

                    # 3. Check if the transaction is older than 2 minutes
                    if time_difference >= timedelta(minutes=2):
                        print(f"Found pending transaction {transaction_key} (UTR: {utr}) for user {user_id} older than 2 minutes. Approving...")
                        approve_transaction_logic(user_id, transaction_key, transaction_data)
                    else:
                        remaining_time = timedelta(minutes=2) - time_difference
                        # print(f"Transaction {transaction_key} (UTR: {utr}) for user {user_id} is still within the 2-minute window. Remaining: {remaining_time}")
                except ValueError as ve:
                    print(f"Could not parse date '{date_str}' for transaction {transaction_key} (user: {user_id}): {ve}")
                except Exception as e:
                    print(f"An unexpected error occurred while processing transaction {transaction_key} (user: {user_id}): {e}")
            elif status == 'Pending' and (not utr or not date_str):
                print(f"Skipping transaction {transaction_key} (user: {user_id}) due to missing UTR or date. Data: {transaction_data}")

# Main loop to run the process periodically
if __name__ == "__main__":
    # Ensure Firebase is initialized before starting the loop
    if firebase_admin._apps:
        print("Starting auto-approval script. Press Ctrl+C to stop (if running locally).")
        while True:
            process_pending_transactions()
            print("Sleeping for 60 seconds before next check...")
            time.sleep(60) # Checks for pending transactions every 1 minute
    else:
        print("Firebase was not initialized. Cannot start the auto-approval script.")
