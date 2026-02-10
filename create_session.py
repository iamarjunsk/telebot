#!/usr/bin/env python3
"""
Script to create Instagram session file manually.
Run this first if Instagram login fails in the main bot.
"""
import os
import getpass
from pathlib import Path
from dotenv import load_dotenv
import instaloader

load_dotenv()

IG_USERNAME = os.getenv('ig_username')
IG_PASSWORD = os.getenv('ig_pass')

def create_session():
    if not IG_USERNAME or not IG_PASSWORD:
        print("❌ Error: ig_username and ig_pass not found in .env file")
        return False
    
    session_file = Path(f'session-{IG_USERNAME}')
    
    print(f"\n🔐 Creating Instagram session for: {IG_USERNAME}")
    print("-" * 50)
    
    loader = instaloader.Instaloader()
    
    try:
        # Try to login
        loader.login(IG_USERNAME, IG_PASSWORD)
        
        # Save session
        loader.save_session_to_file(str(session_file))
        print(f"✅ Session created and saved to: {session_file}")
        print("✅ You can now run the bot!")
        return True
        
    except instaloader.exceptions.TwoFactorAuthRequiredException:
        print("\n⚠️  Two-factor authentication required!")
        print("Please either:")
        print("1. Disable 2FA on your Instagram account temporarily")
        print("2. Or login from your browser and export cookies")
        return False
        
    except instaloader.exceptions.BadCredentialsException:
        print("\n❌ Invalid username or password")
        print("Please check your .env file")
        return False
        
    except instaloader.exceptions.ConnectionException as e:
        print(f"\n❌ Connection error: {e}")
        print("Instagram might be blocking this IP or there's a network issue")
        return False
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        print("\nCommon fixes:")
        print("1. Make sure your credentials are correct in .env")
        print("2. Try logging in from browser first")
        print("3. Wait a few minutes and try again")
        print("4. Instagram might require you to verify the login")
        return False

if __name__ == "__main__":
    print("🤖 Instagram Session Creator")
    print("=" * 50)
    
    if create_session():
        print("\n🎉 Success! You can now run: python bot.py")
    else:
        print("\n❌ Failed to create session")
        print("\nTroubleshooting:")
        print("1. Check your Instagram username and password in .env")
        print("2. Make sure you can login at instagram.com")
        print("3. If you recently changed password, update .env")
        print("4. Instagram might be blocking automated logins")
        print("5. Try logging in from the same IP via browser first")
