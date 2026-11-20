"""DealFlow next-step rules — ONE table, one file.

Every logged outcome maps to exactly one next action and one date. Nothing else in the
repo decides a next step. A note that carries an explicit `next` always wins over anything
computed here, so the date can be overridden by hand at any time.

This module only answers "what is the next step, and when". It never sends, mails, dials,
prints or spends. It is pure: no file I/O, no network, no clock beyond the `today` you pass.

The table is emitted as JS by as_js() and baked into the board, so the browser and the tests
read the same rules. Do not copy these numbers into tracker_template.html by hand.
"""

from datetime import date, timedelta

# --- the one table --------------------------------------------------------------
# outcome key -> (channel, days_from_today, label)
#   channel: 'call' | 'letter' | 'door' | 'appt' | 'none'
#   days:    int, or None when the date comes from the lead (callback time, appointment day)
#
# DRAFT DEFAULTS — Alejandro to confirm. Marked unconfirmed in the PR.
RULES = {
    "no_answer":            ("call",   2,    "Call again in 2 days"),
    "no_answer_3":          ("letter", 0,    "Third no answer, no conversation: letter"),
    "letter_mailed":        ("call",   5,    "Letter mailed: call in 5 days"),
    "letter_2nd":           ("letter", 7,    "Second letter at 7 days"),
    "letter_2nd_nocontact": ("door",   0,    "Second letter, no contact, knock-eligible: door"),
    "talked":               ("call",   3,    "Talked: call in 3 days"),
    "callback":             ("call",   None, "Callback at the time they gave"),
    "appointment":          ("appt",   None, "Appointment day"),
    "appt_held":            ("call",   1,    "Held: call next day"),
    "appt_noshow":          ("call",   1,    "No-show: call next day"),
    "appt_signed":          ("none",   None, "Signed: no further step"),
    "appt_declined":        ("none",   None, "Declined: no further step"),
}

# The four taps after an appointment. Each has a dated next step above.
APPT_OUTCOMES = (
    ("held",     "Held"),
    ("noshow",   "No-show"),
    ("signed",   "Signed"),
    ("declined", "Declined"),
)

# The pipeline strip on the Today page.
PIPELINE = ("Conversations", "Appointments set", "Held", "Signed")

# --- his week -------------------------------------------------------------------
# Dials Mon-Fri. Letters Mon and Wed. Doors Tue, Thu and Sat. Never Sunday.
CHANNEL_DAYS = {
    "call":   {0, 1, 2, 3, 4},        # Mon-Fri
    "letter": {0, 2},                 # Mon, Wed
    "door":   {1, 3, 5},              # Tue, Thu, Sat
    "appt":   {0, 1, 2, 3, 4, 5},     # any day but Sunday
    "none":   {0, 1, 2, 3, 4, 5, 6},
}
NEVER = 6                             # Sunday, no channel

# No letter when the sale is under 7 days out. Mirrors call_mode.ROUTE_MAIL_MIN_DAYS.
MAIL_MIN_DAYS = 7


def next_open_day(channel, d):
    """Push `d` forward to the next day this channel is worked. Never returns a Sunday."""
    allowed = CHANNEL_DAYS.get(channel, set(range(7)))
    for _ in range(14):
        if d.weekday() in allowed and d.weekday() != NEVER:
            return d
        d += timedelta(days=1)
    return d


def _iso(d):
    if d is None:
        return None
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def plan(outcome, today=None, sale_days=None, knock_eligible=False,
         no_answer_streak=0, override=None, gave_time=None, appt_day=None):
    """Resolve one logged outcome to {'ch', 'date', 'lbl', 'why'}.

    override     - {'ch':..., 'date':'YYYY-MM-DD'} wins over everything. A hand-set date.
    sale_days    - days until the sale, or None when unknown. Guards the letter window.
    knock_eligible - whether a door is available for this lead.
    no_answer_streak - consecutive no-answers with no conversation. 3 escalates to a letter.
    gave_time / appt_day - the date the lead gave, for callback / appointment.
    """
    today = today or date.today()

    if override:
        return {"ch": override.get("ch"), "date": _iso(override.get("date")),
                "lbl": "Set by hand", "why": "override"}

    key = outcome
    if outcome == "no_answer" and no_answer_streak >= 3:
        key = "no_answer_3"
    if key not in RULES:
        return {"ch": None, "date": None, "lbl": "Unknown outcome", "why": "no rule"}

    ch, days, lbl = RULES[key]

    # A door only exists if the address is knock-eligible; otherwise the second letter stands.
    if key == "letter_2nd_nocontact" and not knock_eligible:
        ch, days, lbl = RULES["letter_2nd"]

    # No letter inside the mail window. Fall back to a door, then a call.
    if ch == "letter" and sale_days is not None and sale_days < MAIL_MIN_DAYS:
        if knock_eligible:
            ch, days, lbl = "door", 0, "Sale under 7 days: knock instead of letter"
        else:
            ch, days, lbl = "call", 1, "Sale under 7 days: call instead of letter"

    if ch == "none":
        return {"ch": "none", "date": None, "lbl": lbl, "why": key}

    # The date comes from the lead, not from the table.
    if days is None:
        given = gave_time if key == "callback" else appt_day
        if given is None:
            return {"ch": ch, "date": None, "lbl": lbl, "why": key + " (date from the lead)"}
        return {"ch": ch, "date": _iso(given), "lbl": lbl, "why": key}

    d = next_open_day(ch, today + timedelta(days=days))
    return {"ch": ch, "date": d.isoformat(), "lbl": lbl, "why": key}


# --- emitted into the board -----------------------------------------------------
_JS_HELPERS = r"""
function dfToday(){ var d = new Date(); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
function dfAddDays(iso, n){ var d = new Date(iso+'T12:00:00'); d.setDate(d.getDate()+n); return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0'); }
function dfNextOpenDay(ch, iso){
  var allowed = DF_CHANNEL_DAYS[ch] || [0,1,2,3,4,5,6], d = new Date(iso+'T12:00:00');
  for(var i=0;i<14;i++){
    var w = d.getDay();
    if(allowed.indexOf(w) >= 0 && w !== DF_NEVER) return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
    d.setDate(d.getDate()+1);
  }
  return iso;
}
function dfPlan(outcome, opt){
  opt = opt || {};
  var today = opt.today || dfToday();
  if(opt.override) return {ch: opt.override.ch, date: opt.override.date || null, lbl:'Set by hand', why:'override'};
  var key = outcome;
  if(outcome === 'no_answer' && (+opt.no_answer_streak||0) >= 3) key = 'no_answer_3';
  var R = DF_RULES[key];
  if(!R) return {ch:null, date:null, lbl:'Unknown outcome', why:'no rule'};
  var ch = R.ch, days = R.days, lbl = R.lbl;
  if(key === 'letter_2nd_nocontact' && !opt.knock_eligible){ var R2 = DF_RULES['letter_2nd']; ch = R2.ch; days = R2.days; lbl = R2.lbl; }
  if(ch === 'letter' && typeof opt.sale_days === 'number' && opt.sale_days < DF_MAIL_MIN_DAYS){
    if(opt.knock_eligible){ ch='door'; days=0; lbl='Sale under 7 days: knock instead of letter'; }
    else { ch='call'; days=1; lbl='Sale under 7 days: call instead of letter'; }
  }
  if(ch === 'none') return {ch:'none', date:null, lbl:lbl, why:key};
  if(days === null || days === undefined){
    var given = (key === 'callback') ? opt.gave_time : opt.appt_day;
    if(!given) return {ch:ch, date:null, lbl:lbl, why:key+' (date from the lead)'};
    return {ch:ch, date:given, lbl:lbl, why:key};
  }
  return {ch:ch, date: dfNextOpenDay(ch, dfAddDays(today, days)), lbl:lbl, why:key};
}
"""


def as_js():
    """The table + helpers as one JS block, for baking into the board."""
    import json
    rules = {k: {"ch": v[0], "days": v[1], "lbl": v[2]} for k, v in RULES.items()}
    days = {k: sorted(v) for k, v in CHANNEL_DAYS.items()}
    return (
        "var DF_RULES = " + json.dumps(rules, sort_keys=True) + ";\n"
        "var DF_CHANNEL_DAYS = " + json.dumps(days, sort_keys=True) + ";\n"
        "var DF_NEVER = " + str(NEVER) + ";\n"
        "var DF_MAIL_MIN_DAYS = " + str(MAIL_MIN_DAYS) + ";\n"
        + _JS_HELPERS
    )


if __name__ == "__main__":
    import sys
    if "--js" in sys.argv:
        print(as_js())
    else:
        for k in RULES:
            print(k, "->", plan(k))