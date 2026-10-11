"""ADK IT helpdesk: a coordinator that hands off to two specialist agents,
or (variant b) one agent holding all six tools. Data is in memory."""

import copy
from datetime import date

from google.adk.agents import Agent

from harness import model_and_config

_USERS = {
    "maria@example.com": {"name": "Maria Silva", "locked": True, "reset_sent": False},
    "raj@example.com": {"name": "Raj Patel", "locked": False, "reset_sent": False},
    "sam@example.com": {"name": "Sam Okafor", "locked": True, "reset_sent": False},
}
_DEVICES = {
    "LT-1001": {"model": "Dell Latitude 7450", "owner": "maria@example.com", "warranty_end": "2028-06-30"},
    "LT-1002": {"model": "MacBook Pro 14", "owner": "raj@example.com", "warranty_end": "2025-03-31"},
    "LT-1003": {"model": "HP EliteBook 840", "owner": "sam@example.com", "warranty_end": "2027-11-15"},
    "LT-1004": {"model": "Lenovo ThinkPad X1 Carbon", "owner": "raj@example.com", "warranty_end": "2027-09-30"},
}

USERS, DEVICES, TICKETS = {}, {}, []


def reset():
    """Put the data back to its starting state (called before each conversation)."""
    USERS.clear(), USERS.update(copy.deepcopy(_USERS))
    DEVICES.clear(), DEVICES.update(copy.deepcopy(_DEVICES))
    TICKETS.clear()


reset()


def lookup_user(email: str) -> dict:
    """Look up a user account by email address.

    Args:
        email: The user's email address.

    Returns:
        The user's name and whether the account is locked, or an error.
    """
    user = USERS.get(email.strip().lower())
    if not user:
        return {"error": f"No account found for {email}."}
    return {"email": email, "name": user["name"], "locked": user["locked"]}


def unlock_account(email: str) -> dict:
    """Unlock a locked user account.

    Args:
        email: The email address of the account to unlock.

    Returns:
        Confirmation, or the reason nothing was done.
    """
    user = USERS.get(email.strip().lower())
    if not user:
        return {"error": f"No account found for {email}."}
    if not user["locked"]:
        return {"refused": "Account is not locked."}
    user["locked"] = False
    return {"unlocked": True}


def reset_password(email: str) -> dict:
    """Send a password-reset link to a user.

    Args:
        email: The email address of the account.

    Returns:
        Confirmation that the link was sent, or the reason it was not.
    """
    user = USERS.get(email.strip().lower())
    if not user:
        return {"error": f"No account found for {email}."}
    if user["locked"]:
        return {"refused": "Account is locked; unlock it before resetting the password."}
    user["reset_sent"] = True
    return {"reset_link_sent": True, "expires_in": "30 minutes"}


def lookup_device(asset_tag: str) -> dict:
    """Look up a company device by its asset tag.

    Args:
        asset_tag: The asset tag on the device (e.g. "LT-1001").

    Returns:
        The device model and owner, or an error.
    """
    device = DEVICES.get(asset_tag.strip().upper())
    if not device:
        return {"error": f"No device found with asset tag {asset_tag}."}
    return {"asset_tag": asset_tag, "model": device["model"], "owner": device["owner"]}


def check_warranty(asset_tag: str) -> dict:
    """Check whether a device is still under warranty.

    Args:
        asset_tag: The asset tag on the device.

    Returns:
        The warranty end date and whether it is still in warranty, or an error.
    """
    device = DEVICES.get(asset_tag.strip().upper())
    if not device:
        return {"error": f"No device found with asset tag {asset_tag}."}
    return {"warranty_end": device["warranty_end"],
            "in_warranty": date.fromisoformat(device["warranty_end"]) >= date.today()}


def create_repair_ticket(asset_tag: str, issue: str) -> dict:
    """Open a repair ticket for a device that is under warranty.

    Args:
        asset_tag: The asset tag on the device.
        issue: A one-line description of the fault.

    Returns:
        The ticket number, or the reason the ticket was refused.
    """
    tag = asset_tag.strip().upper()
    device = DEVICES.get(tag)
    if not device:
        return {"error": f"No device found with asset tag {asset_tag}."}
    if date.fromisoformat(device["warranty_end"]) < date.today():
        return {"refused": f"Warranty ended {device['warranty_end']}; out-of-warranty devices are replaced, "
                           "not repaired. Ask the user to raise a replacement request with their manager."}
    TICKETS.append({"asset_tag": tag, "issue": issue})
    return {"ticket": f"REP-{1000 + len(TICKETS)}", "turnaround": "3 business days"}


ACCOUNT_TOOLS = [lookup_user, unlock_account, reset_password]
DEVICE_TOOLS = [lookup_device, check_warranty, create_repair_ticket]

STYLE = "Use the tools for every fact and every action; never guess. Give short, direct answers."
ACCOUNT_RULES = "Accounts: you can look up a user, unlock a locked account and send a password-reset link."
DEVICE_RULES = "Devices: you can look up a device, check its warranty and open a repair ticket."


def build_agent(variant: str) -> Agent:
    model, config = model_and_config()
    if variant == "b":      # one agent, all six tools
        return Agent(
            name="helpdesk",
            model=model,
            instruction=f"You are the company IT helpdesk. {STYLE}\n{ACCOUNT_RULES}\n{DEVICE_RULES}",
            tools=ACCOUNT_TOOLS + DEVICE_TOOLS,
            generate_content_config=config,
        )
    account_agent = Agent(
        name="account_agent",
        model=model,
        description="Handles user accounts: lockouts and password resets.",
        instruction=f"You are the account specialist on the company IT helpdesk. {STYLE}\n{ACCOUNT_RULES}\n"
                    "If the user also needs help with a device, hand over to device_agent when you are done.",
        tools=ACCOUNT_TOOLS,
        generate_content_config=config,
    )
    device_agent = Agent(
        name="device_agent",
        model=model,
        description="Handles company devices: device details, warranty checks and repair tickets.",
        instruction=f"You are the device specialist on the company IT helpdesk. {STYLE}\n{DEVICE_RULES}\n"
                    "If the user also needs help with an account, hand over to account_agent when you are done.",
        tools=DEVICE_TOOLS,
        generate_content_config=config,
    )
    return Agent(
        name="helpdesk_coordinator",
        model=model,
        instruction="You are the front desk of the company IT helpdesk. You have no tools of your own: "
                    "hand each request to the specialist that handles it.",
        sub_agents=[account_agent, device_agent],
        generate_content_config=config,
    )
