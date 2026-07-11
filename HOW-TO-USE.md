# How to Use the Foreclosure Lead Tool 🏠

**The website:** https://aiejandrog.github.io/foreclosure-leads/

This is a list of houses in Miami-Dade where the owner is about to lose their home at a
foreclosure auction. Some of them have a LOT of value and only owe a little bit — those are the
ones we want to help (and make money on). The website finds them for us automatically.

---

## What am I looking at?

Every house is one row. Reading left to right, the important stuff is:

- **Est. Profit** (big green number) — how much money we could make on this house. Biggest is on top.
- **Score** — how *good/easy* the lead is (0–100). High = the owner has lots of equity and time.
- **Auction** — the day the house gets sold at auction. "in 5d" means 5 days away. Red = hurry!
- **Owner** — the person who owns the house (who we mail/call).
- **vs Plaintiff** — who is foreclosing (a bank, or an HOA/condo association).
- **Property** — the address, and a small line: what it's worth, what they owe, and their equity.
- **Links** — buttons that open helpful websites (explained below).

> 💡 On your **phone**, it turns into easy-to-read cards instead of a wide table. Same info.

---

## The buttons on each house (the "Links")

Click these to dig into any house:

- **Zillow** — see photos and guesses of the home's value.
- **Appraiser** — the official county page: who really owns it + their mailing address.
- **Auction** — the official auction page for that sale.
- **People** — opens a people-search with the owner's name filled in, to find their **phone number**.
  (You have to look at the result yourself — the site blocks robots, so I can't grab the phone for you.)
- **Taxes** — shows if they owe back taxes on the property.
- **Case ▸** — opens the actual court case: the full story, all the people involved, and the judgment.
- **✉ Letter** — makes a ready-to-print letter for that owner (see below). This is the money button.
- **📋 (next to an address)** — copies the WHOLE address (even the part cut off by "...").
- **📋 Copy all** — copies everything about the house (owner, address, mailing, case #, auction) at once.

---

## The 3 things you'll do most

### 1. Find the best houses to work
The list is already sorted with the most profitable on top. Want the urgent ones?
Click **⚡ This week** to see only houses with an auction in the next 7 days. Act on those first.

### 2. Send a letter to the owner
1. **One time only:** at the top, fill in the **"Letter sender"** boxes with Jose's name, LLC, and phone.
   (These letters go out under Jose's name — never yours.)
2. Click **✉ Letter** on any house.
3. A letter pops up already written and filled in. Pick **English or Español**.
4. Click **🖨 Print / Save PDF**. Mail it to the address shown.

### 3. Keep track of who you called
Each house has a **Status** dropdown (Letter sent, Called, Appointment, Dead, etc.) and a **Notes** box.
Use them! They save automatically on your own device. Click **Hide dead** to clean up your list.

> ⚠️ Your Status and Notes are saved **in your own browser only** — they don't share between your
> phone and computer, or between you and Jose. To hand off progress, click **Export CSV** and send that file.

---

## Before you make any offer — 2 rules

1. **Always click "Case ▸" and check the court docket first.** The "owed" number might be missing a
   big mortgage hiding underneath. If you see **⚠ mortgage risk** on a house, that means a bank is
   also owed money — the real payoff is higher than it looks. Verify before you promise anything.
2. **Letters and calls go under Jose's name and LLC — never Alejandro's name, money, or bank accounts.**

---

## How does the list stay fresh?

It **updates itself every Monday at 9:00 AM** (your computer just needs to be on). New houses show up,
sold/canceled ones drop off. Want it updated right now? Double-click **`run-leads.bat`** in the
`foreclosure-leads` folder and wait a few minutes.

---

## Quick cheat sheet

| I want to… | Do this |
|---|---|
| See the best deals | Just open the site — top of the list |
| See urgent ones | Click **⚡ This week** |
| Find the owner's phone | Click **People** |
| Read the court case | Click **Case ▸** |
| Send a letter | Fill in sender once → click **✉ Letter** → Print |
| Mark a lead worked | Use the **Status** dropdown |
| Give the list to someone | Click **Export CSV** |
| Refresh right now | Double-click **run-leads.bat** |
