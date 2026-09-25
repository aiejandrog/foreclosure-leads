"""case_verdict — emit the per-case verdict a person has been writing by hand.

WHY THIS EXISTS
MIAMI-AUTOMATION-STATUS.md's five-case table carries a verdict column — supported, incomplete,
conflicted — and said of it, until the commit that added this module: "The verdict column is written
from the code's output by hand: no module emits supported / incomplete / conflicted yet." That
sentence was the last thing between the pipeline and the acceptance matrix's "Twelve-case review:
explicit review-policy acceptance and unattended evidence report", which is still marked not passed.
A human reading nine kinds of JSON and typing a word is not an unattended report, and it does not
scale past twelve cases.

WHAT THIS IS ALLOWED TO DO
Restate, in one word plus its reasons, states that other modules already computed. It reads nothing,
fetches nothing, spends nothing, and decides nothing on its own:

    conflicted   two pieces of evidence disagree, and no read document resolves which is right
    incomplete   something needed was not read, not reached, or not verified
    supported    the controlling judgment, the posture and the amount each rest on a read document

It never upgrades a state. Every reason it prints is the producing module's own string, so the
vocabularies stay comparable (the same fact has up to five names across the pipeline — see the
NAMES table below). A verdict is not contact clearance, not an equity input, and not a board field:
`miami_ranking.qualify` remains the gate for who may be called, and equity_state's FACT states are
still reached only through recorded-instrument evidence.

THE SCOPE DECISION, STATED BECAUSE IT WILL BE QUESTIONED
"supported" is scoped to the CONTROLLING JUDGMENT, not to the whole docket. Coverage gaps on
unrelated attachments are listed as notes and do not block it. Two reasons:

  1. It is what the hand-written verdicts mean. 2024-014878 is "supported" in the status table with
     the $1,746,032.70 verified on the judgment's own pages, while other attachments on that docket
     were never read.
  2. Requiring whole-docket completeness makes every case incomplete forever and the word useless.
     `case_dossier`'s `complete` is `not open_gaps`, and one gap is appended whenever
     `pagination_verified` is false — which case_dossier itself documents as "False, always",
     because the clerk publishes no pagination cursor. So a blanket completeness gate is not a
     high standard, it is a constant.

Docket completeness stays exactly what the status file says it is: unproven, and printed as a
standing caveat on every verdict rather than hidden or quietly treated as satisfied.
"""
import argparse
import json
import os
import re
from pathlib import Path

VERDICTS = ('supported', 'incomplete', 'conflicted')
# Reports are read from the top, and a contradiction is what a person needs to see first.
REVIEW_ORDER = ('conflicted', 'incomplete', 'supported')

# The same fact, in each module's own words. Kept as data so a reader can see the drift instead of
# rediscovering it, and so this module can accept either spelling without inventing a third.
NAMES = {
    'login_walled': {'miami_case_timeline': ('login_required', 'login_required_likely'),
                     'document_coverage': ('restricted', 'restricted_likely')},
    'no_image': {'miami_case_timeline': ('no_image_indexed', 'not_fetched', 'missing_attachments'),
                 'document_coverage': ('county_no_document',)},
    # document_coverage :20 defines not_enumerated as "the entry's attachment list itself could not
    # be obtained", and :169 emits it saying how many documents the docket claims. A document exists
    # and was not reached, which is the opposite of having none to read.
    'not_reached': {'document_coverage': ('not_enumerated',)},
    'part_read': {'miami_case_timeline': ('unreadable_pages', 'unassessed_pages'),
                  'document_coverage': ('read_partial',)},
}
LOGIN_WALLED = NAMES['login_walled']['document_coverage'] + NAMES['login_walled']['miami_case_timeline']
NO_IMAGE = NAMES['no_image']['document_coverage'] + NAMES['no_image']['miami_case_timeline']
NOT_REACHED = NAMES['not_reached']['document_coverage']
PART_READ = NAMES['part_read']['document_coverage'] + NAMES['part_read']['miami_case_timeline']

# miami_case_timeline sets status kind 'unclear' for nine different reasons. Three are evidence
# CONTRADICTING evidence; the rest are evidence MISSING. The distinction is the whole point of this
# module, so the strings are matched literally and anything unrecognised counts as missing, which is
# the side that cannot overstate what is known.
CONFLICT_REASONS = (
    'Order vacating the judgment, certificate or sale found; subsequent case posture requires '
    'explicit evidence.',
    'Later foreclosure activity conflicts with an unresolved bankruptcy stay; no relief identified.',
    'Conflicting same-date entries; reliable within-day order is unavailable.',
)
CONFLICTING_JUDGMENTS = 'judgments conflict or could not be linked; review required'
NO_OPERATIVE_JUDGMENT = 'no operative judgment'
# judgment_money's own wording when two different runs of printed rows both reach the total. The
# document supports two readings and nothing in it says which table the figure belongs to.
AMBIGUOUS_RUN = ('more than one run of rows adds up to the total; where the table starts is '
                 'ambiguous, review required')
# judgment_money raises this for BOTH a subtotal whose listed members could not be read and one
# whose members read fine and do not add up to it (:199 and :215). Two different facts, one string.
SUBTOTAL_DISAGREES = 'printed subtotal lacks valid members or disagrees with its own items'

# miami_case_timeline's status kinds, minus the two this module handles on their own ('unclear' and
# 'active_pre_judgment'). An ALLOWLIST: a kind this module has never heard of holds the case, because
# the alternative is that a renamed or added kind silently turns every case into 'supported'.
SETTLED_KINDS = ('judgment_entered', 'sale_scheduled', 'sale_cancelled', 'stayed_by_bankruptcy',
                 'dismissed', 'satisfied_redeemed', 'sold')
# Postures where the docket is moving or has moved a sale. A stay in effect over any of them is a
# contradiction, not a note. The kinds are not enough on their own: when an unresolved stay is
# followed by sale activity, miami_case_timeline (:494) forces kind 'unclear', so this pair is close
# to unreachable in real output and the first version's stay-vs-sale rule was exercised only by a
# fixture shape the pipeline does not write. `_sale_on_the_docket` below reads the sale facts the
# producer actually saves.
# Status kinds that are ABOUT a sale. Read by the vocabulary test only, which checks this module
# vouches for no posture miami_case_timeline cannot emit; the sale-liveness decision is
# SALE_LIVE_STATUS_KINDS, further down, and deliberately excludes 'sold'.
SALE_KINDS = ('sale_scheduled', 'sold')
# status kinds that say a sale is over and settled, so a date in the past is not an open question.
# miami_case_timeline records sale_outcome when a scheduled date passes with no certificate.
UNSETTLED_SALE_OUTCOMES = ('unknown_no_certificate', 'held_no_certificate_yet')
# judgment_money's wording when the stated grand total has no matching printed total row on its own
# page: the document's own table disagrees with the figure it prints as the total.
TOTAL_ROW_DISAGREES = 'missing or disagreeing kind=total'

CAVEAT = ('Docket completeness is unproven: the clerk publishes no pagination cursor, so no run can '
          'show the entry list is whole. A verdict describes the evidence read, never the file.')
QUALIFICATION = (
    'Restates states other modules computed; reads and spends nothing. "supported" covers the '
    'controlling judgment, its posture and its amount - not every attachment on the docket. Not '
    'contact clearance (miami_ranking.qualify), not an equity input (equity_state). An amount is '
    'named by the document it was read off, but nothing saved says which attachment on a docket '
    'entry IS the judgment, so on an entry filed with more than one document the figure is the one '
    'that verified on the named copy, not proof that copy is the judgment.')


def _rows(timeline, key):
    value = timeline.get(key)
    return value if isinstance(value, list) else []


def _dict(timeline, key):
    """A block that must be a dict, or an empty one. Never assume a saved shape.

    `coverage` and `status` were hardened in the first review round and `judgments` and
    `amount_vision` were not, so a list or a string in either raised AttributeError mid-assess and
    took the whole unattended report down with it.
    """
    value = timeline.get(key)
    return value if isinstance(value, dict) else {}


def _amount_checks(timeline):
    """Every exact-cents check saved with this timeline, whichever path produced it."""
    vision = _dict(timeline, 'amount_vision')
    checks = [c for c in (vision.get('amount_checks') or []) if isinstance(c, dict)]
    return checks + [c for c in _rows(timeline, 'amount_checks') if isinstance(c, dict)]


def _controlling(timeline):
    judgments = _dict(timeline, 'judgments')
    return judgments.get('controlling_entry'), judgments


def _judgment_record(judgments, entry_id):
    """The controlling judgment's own row in reconcile_judgments' list, or None.

    Its `status` is not always 'operative': `operative` there includes 'partially_vacated'
    (miami_case_timeline :753), and `satisfaction` can be 'partially_satisfied'. A judgment that is
    partly vacated and partly satisfied was reading as a clean verified amount.
    """
    rows = judgments.get('judgments')
    if entry_id is None or not isinstance(rows, list):
        return None
    return next((r for r in rows if isinstance(r, dict) and r.get('entry_id') == entry_id), None)


# Docket entry kinds that put a sale on the calendar, and the ones that take it off. Both lists are
# miami_case_timeline.classify's own labels (:193-203). certificate_of_sale and certificate_of_title
# were missing from the closing list until the seventh review: a notice of sale followed by a
# certificate of title read as a sale still going ahead, and under a stay that is a false conflict.
SALE_NOTICE_KINDS = ('notice_of_sale', 'order_resetting_sale')
SALE_CLOSING_KINDS = ('order_cancelling_sale', 'certificate_of_sale', 'certificate_of_title')
# Status kinds that say a sale is on the calendar NOW. 'sold' is not one of them: it is what a
# certificate produces, and a completed sale is not a sale going ahead.
SALE_LIVE_STATUS_KINDS = ('sale_scheduled',)
# The clerk's own sale-day rows and the two certificates, as miami_case_timeline.sale_held keys on
# them (:543, :550) - re-read here through _producer_labels because that function does not.
SALE_MONEY_KINDS = ('sale_bid', 'sale_deposit')
CERTIFICATE_KINDS = ('certificate_of_sale', 'certificate_of_title')
# The kinds classify uses when it did NOT recognise the entry: 'other' is its fallthrough (:223) and
# 'hearing' is the override a calendar eventType forces onto anything but a notice of sale (:383).
# An entry the producer DID label is explained by its label - the petition that names the sale it
# stays is not an unclassified sale entry, which is how a docket with no sale on it read incomplete.
# The producer's own COVER labels: what classify returns for a filing titled ABOUT something else
# when nobody opened the document ("A notice ABOUT a judgment is not the judgment", :146). They name
# the envelope and not the subject, so for the sale scan they are exactly as unlabelled as 'other' -
# and leaving them out is how a cover-titled notice of sale read `supported` while the same notice
# under its own title read incomplete (twenty-first review).
COVER_KINDS = ('notice_of_filing', 'certificate_of_service', 'affidavit')
UNLABELLED_KINDS = ('other', 'hearing') + COVER_KINDS
# Only to notice that the classifier left a sale-worded entry unlabelled, which is reported as a
# gap. Never to decide that a sale IS or IS NOT scheduled - see _sale_state.
_SALE_WORD_RE = re.compile(r'\bsale\b', re.I)
# miami_case_timeline's own reset vocabulary (:31). An entry saying a sale was reset or
# rescheduled is a resale whether or not anything saved prints its new date: the date may be in
# a document behind the county login, which is the ordinary case for a sale notice. The clerk's
# proceeds entries after a completed sale carry neither word.
_RESET_WORD_RE = re.compile(r'\b(?:reset|reschedul\w*)\b', re.I)
# What an entry must say to be a candidate for CLOSING or MOVING the sale now on the calendar. The
# question those branches ask is narrow, and every phrasing they exist for carries one of these.
_CLOSING_WORD_RE = re.compile(r'\b(?:cancel\w*|vacat\w*|withdraw\w*|reset|reschedul\w*|'
                              r'continu\w*|postpon\w*)\b', re.I)
# Entry kinds that RAISE a stay (miami_case_timeline :469, :553). Relief, dismissal and discharge
# are stay-ENDING events: one of those dated after the run's as_of says nothing about the stay state
# at as_of, and counting them made a clean case incomplete.
BANKRUPTCY_KINDS = ('suggestion_of_bankruptcy', 'stay', 'stay_reinstated')


def _producer_labels(entry):
    """Every kind the PRODUCER assigned this entry: `kind` and `index_kind` both.

    `kind` is not a reliable carrier of the producer's classification. miami_case_timeline :383 does
    `if e['calendar_event'] and e['kind'] != 'notice_of_sale': e['kind'] = 'hearing'`, so any entry
    whose OCS eventType is a hearing has its real label overwritten and keeps it only in
    `index_kind`. The eighth review found that this module knew about the override in one of the three
    places it reaches: a suggestion of bankruptcy on a hearing event read `supported` with "none on
    the docket" printed in the stay column, over a live 11 USC 362 stay. Matching both keys still only
    restates a label the producer computed.

    `index_kind` is read ONLY where the override actually fired, because it is the WEAKER label
    everywhere else. `kind = body_kind or ik` (:361): when the producer opened the document, `kind` is
    what the document says and `index_kind` is only the clerk's index. Unioning the two let the index
    outrank a read document in both directions (ninth review): a docket line saying "Certificate of
    Sale" whose own document reads "NOTICE OF FORECLOSURE SALE" closed a live sale under a §362 stay
    and returned `supported`, and a line saying "Notice of Filing Bankruptcy Petition" whose document
    reads "ORDER DENYING MOTION TO COMPEL" produced an `incomplete` no further reading can clear.
    `kind_source` cannot be the test - :383 does not update it - but `calendar_event` is on the entry
    and is exactly the condition the override fires on.

    THE PRODUCER'S OWN stay_history HAS THE SAME BUG (:462 keys on e['kind']), so its backstop is
    defeated by the same docket. That is not fixed here: it is on miami_case_timeline, it would move
    §362 stay flags the board hard-gates on, and CLAUDE.md's rule for a bug found from another
    session's surface is to report it rather than fix it. Reported to Alejandro with this PR.
    """
    kind = entry.get('kind')
    if kind == 'hearing' and entry.get('calendar_event'):
        # The override fired and took the real label with it.
        if entry.get('kind_source') == 'document':
            # What it took was the DOCUMENT's label, and that is recoverable: :360 sets
            # operative_text to the very title line _body_kind classified. Falling back to
            # index_kind here was a regression that reopened this module's worst case - an order
            # DENYING a motion to cancel a sale, indexed "Order Cancelling Foreclosure Sale", closed
            # a live sale under a Chapter 13 stay and returned `supported` (twelfth review). The
            # producer also saves index_agrees=False on exactly these entries.
            recovered = _classify(entry.get('operative_text'))
            if recovered:
                return (recovered,)
        # 'docket_text' means kind WAS index_kind, so the index is the real label. 'document_passage'
        # (:377) overwrote operative_text, so nothing is recoverable - and _relabelled holds that
        # branch unconditionally.
        return tuple(k for k in (entry.get('index_kind'),) if k) or ('hearing',)
    return tuple(k for k in (kind,) if k)


def _after_cutoff(entry, as_of):
    """True when this entry is dated after the run's as_of, so it says nothing about the case AT it.

    An UNDATED entry is never dropped. The producer skips both undated and later entries where it
    decides a posture (:446, reconcile_judgments :675) and compensates for the undated half by forcing
    status 'unclear' - but only for entries `_transition` recognises. Copying that skip into a gap
    check emptied it: an undated "Notice of Rescheduled Foreclosure Sale", the classifier's own known
    blind spot, stopped raising its gap and a case under a live stay read `supported` (fourteenth
    review). An entry with no date is more unknown, not less, and every check below raises gaps.
    """
    date = str(entry.get('date') or '')
    return bool(date) and date > str(as_of or '9999-99-99')


def _certificate_at_cutoff(timeline, held):
    """-> (the held sale's certificate entry as of the run's cutoff, the one excluded for being later).

    `sale_held` bounds its money-row scan by the run's as_of (miami_case_timeline :544); its
    certificate scan (:550) does NOT - it only requires a date at or after the held sale, and
    build_timeline keeps post-as_of entries in `entries`. So a certificate dated AFTER the run's own
    cutoff sets sale_held['certificate'], and reading that bare field as "the sale closed" suppressed
    the held-sale gap: a sale the clerk held before the cutoff, under a live bankruptcy stay, read
    `supported` with nothing on the page saying a sale had been held (fifteenth review). The missing
    bound was already known where this module RAISES a gap (see _labelled below); the half that
    SUPPRESSES one was not.
    """
    ident = (held or {}).get('certificate')
    if ident is None:
        return None, None
    row = next((e for e in _rows(timeline, 'entries')
                if isinstance(e, dict) and str(e.get('entry_id')) == str(ident)), None)
    if row is not None and _after_cutoff(row, timeline.get('as_of')):
        return None, ident
    return ident, None


def _labelled(timeline, kinds, since=None):
    """-> the entries the producer gave one of `kinds`, read through _producer_labels.

    `timeline['sale_held']` is a producer SUMMARY computed from a bare `e['kind']`
    (miami_case_timeline :543 for the clerk's money rows, :550 for the certificate), so the :383
    override silently empties it: sale-day bid and deposit rows on a calendar event made sale_held
    None and the verdict `supported` over a sale the saved file says was held, and a calendar-typed
    certificate made the report print "no certificate of sale has followed" about a docket carrying
    one. A summary is only as good as the field it was built from, so the entries are re-read here.
    """
    since = str(since or '')
    # The producer's money-row scan bounds by as_of (:544); its certificate scan does NOT (:550), it
    # only requires a date at or after the held sale. Mirroring a bound the producer does not have
    # drops evidence, so the upper bound applies only where the producer has one.
    until = '9999-99-99' if since else str(timeline.get('as_of') or '9999-99-99')
    out = []
    for e in _rows(timeline, 'entries'):
        if not isinstance(e, dict) or not set(_producer_labels(e)) & set(kinds):
            continue
        # The producer's own filters, or this re-read invents evidence the summary correctly left
        # out: sale_held takes a money row only when it has a date at or before the run's as_of
        # (:544) and a certificate only when it is dated ON OR AFTER the held sale (:550). Without
        # the second, a certificate from a sale two years earlier downgraded a live stay-against-sale
        # contradiction to a gap, with a reason saying the summary had not taken it in when the
        # summary had considered it and correctly excluded it (tenth review).
        date = str(e.get('date') or '')
        if not date or date > until or (since and date < since):
            continue
        out.append(e)
    return out


# Producer labels that decide a case's posture, and so the summaries the verdict rests on. Any of
# these lost to the :383 relabel is a gap: this module does not guess which one the entry was.
DECIDING_KINDS = ('vacatur', 'satisfaction', 'final_judgment', 'order_of_dismissal',
                  'notice_of_voluntary_dismissal', 'sale_bid', 'sale_deposit',
                  'certificate_of_sale', 'certificate_of_title', 'notice_of_sale',
                  'order_resetting_sale', 'order_cancelling_sale', 'suggestion_of_bankruptcy',
                  'stay', 'stay_reinstated', 'relief_from_stay', 'bankruptcy_dismissed',
                  'bankruptcy_discharged')
DOCUMENT_SOURCES = ('document', 'document_passage')


def _relabelled(timeline):
    """-> [(entry, what the relabel cost)] for entries :383 took a deciding label from.

    Ten review rounds went one site at a time: the stay history, then the held-sale summary and the
    certificate, then the bankruptcy-on-the-sale-day list. Every one was the same relabel reaching a
    different summary, and the tenth round found three more - a vacated judgment and a satisfied one
    reported `supported` with the amount "verified to the cent", and an order resetting a sale under a
    live stay reported `supported` with the sale never named. So this stops chasing sites: where the
    relabel took a posture-deciding label, the case is held as a gap, whatever summary consumed it.

    THIS MODULE DOES NOT DECIDE WHAT THE ENTRY WAS. A calendar event genuinely can be a hearing ON a
    motion rather than the thing itself, which is what :383 is for, and nothing in the saved file says
    which this is. Naming the possibility and holding the case is the only answer the file supports;
    deciding it needs the clerk's real data, which is the desktop's to measure.
    """
    out = []
    for e in _rows(timeline, 'entries'):
        if (not isinstance(e, dict) or e.get('kind') != 'hearing'
                or not e.get('calendar_event') or _after_cutoff(e, timeline.get('as_of'))):
            continue
        index_kind = e.get('index_kind')
        source = e.get('kind_source')
        if source == 'document_passage':
            # The stay_reinstated path (:377) overwrote operative_text with the passage, so the label
            # is not recoverable - and that path only ever carries a deciding label anyway.
            out.append((e, 'the label a read document passage gave it, which is saved nowhere'))
        elif source == 'document':
            # kind came from reading the document, so what the relabel destroyed was the DOCUMENT's
            # own label. It IS recoverable: :360 sets operative_text to the very title line
            # _body_kind classified, so the producer's own parser gives the label back. Claiming it
            # was "saved nowhere" and holding every case with a read document on a calendar event
            # made an ordinary docket incomplete for good - a notice of appearance, an answer, even
            # an order SETTING a hearing, where the relabel was a no-op (eleventh review).
            lost = _classify(e.get('operative_text'))
            if lost in DECIDING_KINDS:
                out.append((e, "the read document's own title reads as %s" % lost))
        elif index_kind in DECIDING_KINDS:
            out.append((e, 'the docket index calls it %s' % index_kind))
    return out


def _index_text(entry):
    """The text the PRODUCER classifies on: description + comments (miami_case_timeline :345).

    Reading `description` alone was a false clean bill: a clerk who files a stub description
    ("Notice:") and puts "OF FORECLOSURE SALE SET FOR 12/28/2026" in the comments produced an entry
    that was neither labelled a sale notice nor flagged as unlabelled, so a live sale under a stay
    vanished from the file entirely (_casetimelinetest :362, :461 carry both real shapes).
    """
    return ' '.join(str(entry.get(k) or '') for k in ('description', 'comments'))


def _sale_state(timeline, status, kind):
    """-> ('live'|'unknown'|'none', phrase) for the sale the docket is running.

    THIS MODULE DOES NOT CLASSIFY DOCKET ENTRIES. Seven review rounds on this one question all went
    the same way: reading status['sale_date'] missed a stay filed after the notice (the producer's
    loop does `status = change`, :503, and the bankruptcy branch builds a fresh dict with no sale
    date); reading entry kinds missed "Notice of Rescheduled Foreclosure Sale", which classify
    labels 'other'; reading `sale_passages` matched the judgment's own "shall sell the property" and
    the petition asking the court to stop the sale; reading the entry's own words with a regex
    counted unruled motions, objections and denials as dispositive in both directions. Each patch
    broke the opposite way, because deciding whether a sale is scheduled is a classification job -
    and this module's contract (see the docstring) is to RESTATE what other modules computed, never
    to compute a state of its own.

    So it reads only the producer's own labels, and where those cannot answer, it says so:

      live     the producer itself says a sale is running - its status kind, a sale it recorded as
               held with no certificate yet, or an entry IT labelled a sale notice that no entry IT
               labelled a cancellation or a certificate closes.
      unknown  the docket has sale-worded entries the classifier left unlabelled, so whether a sale
               is pending is not answerable from this file. A gap, never a contradiction.
      none     no sale evidence of either kind, or the newest thing the producer labelled ends a
               sale.

    Two things here ARE this module's own, and the contract above is overstated without them: the
    ordering of the producer's labels by date, and a `\bsale\b` scan of the text the producer itself
    classifies on. The scan can only raise a gap - it never opens or closes a sale - and the ordering
    compares two of the producer's own labels and nothing else.

    Nothing here short-circuits on a derived summary: `sale_held` describes only the most recent held
    sale (`sale_held`, `day = max(...)`), so returning on its certificate hid a resale noticed after
    it, and that was a false clean bill over a live stay.
    """
    opening = closing = None
    unlabelled = []
    # The producer keeps post-as_of entries in `entries` and skips them everywhere it DECIDES
    # anything (:442, reconcile_judgments :671, sale_held :544), and _labelled :249 re-applies that
    # bound. This loop did not, so on an acceptance replay with a cutoff behind the collection date a
    # certificate dated AFTER the cutoff closed a sale that was live at it, and the case read
    # `supported` over a stay (thirteenth review). The mirror over-fired: a later notice of sale made
    # a docket with no sale at the cutoff read conflicted.
    as_of = timeline.get('as_of')
    for entry in _rows(timeline, 'entries'):
        if not isinstance(entry, dict) or _after_cutoff(entry, as_of):
            continue
        labels = set(_producer_labels(entry))
        if labels & set(SALE_CLOSING_KINDS):
            closing = _newer(closing, entry)
        elif labels & set(SALE_NOTICE_KINDS):
            opening = _newer(opening, entry)
        elif labels <= set(UNLABELLED_KINDS) and _SALE_WORD_RE.search(_index_text(entry)):
            # The classifier saw the word and did not label the entry. That is a limit of the
            # classifier, not evidence either way, and it is not this module's to resolve.
            unlabelled.append(entry)
    closing_date = str((closing or {}).get('date') or '')

    def _later_unlabelled(floor, may_close_it=False):
        out = [e for e in unlabelled if str(e.get('date') or '') >= str(floor or '')]
        if may_close_it:
            # The LIVE branches ask one question: could this entry BE the cancellation or the
            # rescheduling of the sale now on the calendar? The phrasings this scan exists for all
            # carry one of these words - "Notice of Cancellation of Foreclosure Sale" (eighteenth
            # review), "Notice of Rescheduled Foreclosure Sale" (seventeenth). The routine paperwork
            # of a noticed sale does not, and it is filed AFTER the notice, so the date floor cannot
            # reach it: "Statement of Amounts Due at Sale" and "Plaintiff's Bid at Sale" held the
            # ordinary live-lead docket incomplete (twentieth review). The final branch below asks a
            # different question - a FRESH notice after a cancellation - and is not filtered.
            out = [e for e in out if _CLOSING_WORD_RE.search(_index_text(e))]
        return out

    # The floor for the LIVE-sale branches. Those ask whether an unlabelled entry might be the
    # cancellation or the rescheduling of the sale now on the calendar, so an entry dated BEFORE the
    # notice that put it there cannot be one - and a routine foreclosure docket carries such entries
    # ("Order Setting Foreclosure Sale", "Plaintiff's Bid at Sale", "Statement of Amounts Due at
    # Sale" all classify as 'other'). Flooring them at closing_date alone, which is '' when nothing
    # closes a sale, held the ordinary live-lead shape incomplete for good and said of a five-month-old
    # entry that it might unsettle a sale noticed later (nineteenth review). The `opening` branch below
    # has always floored at the notice; these now do the same. max() keeps today's behaviour when the
    # producer labelled no notice at all.
    live_floor = max(closing_date, str((opening or {}).get('date') or ''))

    held = timeline.get('sale_held')
    if not (isinstance(held, dict) and held.get('date')):
        # The summary is built from a bare kind, so :383 can empty it while the clerk's own sale-day
        # rows sit in `entries`. Reading it as "no sale was held" replaced the stay-against-sale
        # contradiction with a note (tenth review). The money rows are the producer's labels too.
        money = _labelled(timeline, SALE_MONEY_KINDS)
        if money and closing_date < max(str(e.get('date') or '') for e in money):
            return 'unknown', ("entr%s %s carr%s the clerk's sale-day bid or deposit label while the "
                               "run's own held-sale summary took none of them in, so whether a sale "
                               'was held is not settled in this file'
                               % ('y' if len(money) == 1 else 'ies',
                                  ', '.join(str(e.get('entry_id') or '?') for e in money[:5]),
                                  'ies' if len(money) == 1 else 'y'))
    cert, late_cert = _certificate_at_cutoff(timeline, held)
    if (isinstance(held, dict) and held.get('date') and not cert
            and closing_date < str(held['date'])):
        if [e for e in _labelled(timeline, CERTIFICATE_KINDS, since=held['date'])
                if not _after_cutoff(e, as_of)]:
            # The summary says no certificate; an entry carries a certificate label the summary did
            # not take in. Saying either is a claim this file does not support.
            return 'unknown', ('the run\'s held-sale summary for %s records no certificate while a '
                               'docket entry carries a certificate label it did not take in'
                               % held['date'])
        if late_cert is not None:
            # The summary DID take this certificate in, so the wording above would be false. What is
            # true is that the only certificate for the sale is dated after this run's cutoff.
            return 'live', ('the clerk posted sale-day bid and deposit entries on %s and the only '
                            "certificate the run found for that sale is entry %s, dated after this "
                            "run's as_of" % (held['date'], late_cert))
        return 'live', ('the clerk posted sale-day bid and deposit entries on %s and no '
                        'certificate of sale has followed' % held['date'])
    if kind in SALE_LIVE_STATUS_KINDS:
        # The same test the `opening` branch below applies, and for the same reason: an unlabelled
        # sale-worded entry newer than anything the producer labelled may BE the cancellation or a
        # rescheduling, and classify leaves both of those phrasings 'other'. Returning 'live' off the
        # bare status kind skipped it, so on the one posture where a later unread entry matters most -
        # the live sale itself - the gap was unreachable, and a docket where strictly LESS was known
        # read `supported` where the same entry after a LABELLED cancellation read incomplete
        # (eighteenth review).
        later = _later_unlabelled(live_floor, may_close_it=True)
        if later:
            return 'unknown', ('%s, so whether that sale still stands cannot be told from this file'
                               % _unlabelled_phrase(later))
        return 'live', 'the docket status is %r' % (kind,)
    if status.get('sale_date') and closing_date < str(status['sale_date']):
        # Only reachable if the producer ever writes sale_date on a status kind outside
        # SALE_LIVE_STATUS_KINDS; _transition writes it only for 'sale_scheduled' today, so the
        # branch above wins. Guarded the same way rather than left as the one unguarded path.
        later = _later_unlabelled(live_floor, may_close_it=True)
        if later:
            return 'unknown', ('%s, so whether the sale on the calendar for %s still stands cannot '
                               'be told from this file'
                               % (_unlabelled_phrase(later), status['sale_date']))
        return 'live', 'a sale is on the calendar for %s' % status['sale_date']
    if opening is not None:
        # A cancellation or certificate on the SAME DAY closes it: the docket gives dates, not
        # times, so a notice and a closing entry on one day cannot be ordered, and reporting "no
        # later cancellation" over a docket that carries one is the claim that cannot be defended.
        if closing_date < str(opening.get('date') or ''):
            # An unlabelled sale-worded entry dated on or after the notice may BE the cancellation -
            # "Notice of Cancellation of Foreclosure Sale" is one the classifier leaves as 'other'.
            # So the sale state is unknown, not live: claiming a conflict here would be the same
            # guess in the opposite direction.
            later = _later_unlabelled(opening.get('date'), may_close_it=True)
            if later:
                return 'unknown', ('entry %s is classified as a notice of sale, and %s, so whether '
                                   'that sale still stands cannot be told from this file' % (
                                       opening.get('entry_id') or '?', _unlabelled_phrase(later)))
            dates = _sale_dates_of(opening)
            return 'live', ('entry %s, which the docket classifies as %s (%s%s), with no '
                            'cancellation or certificate on or after it' % (
                                opening.get('entry_id') or '?',
                                next(k for k in _producer_labels(opening)
                                     if k in SALE_NOTICE_KINDS),
                                opening.get('date') or 'undated',
                                '; sale date %s' % ', '.join(dates) if dates else ''))
    # Either the newest labelled sale event ends a sale, or there was never one. In both cases an
    # unlabelled sale-worded entry after it could be a fresh notice this module cannot read - UNLESS
    # the thing that closed the sale was a certificate. A sale that completed is followed by the
    # clerk's proceeds handling, and "Disbursement of Sale Proceeds" and "Surplus Funds from Sale"
    # both carry the word and both classify as 'other', so treating those as a possible fresh notice
    # would read every completed sale as unknown for good (seventeenth review). A cancellation, or
    # nothing at all, leaves room for a later notice; a certificate does not.
    completed = closing is not None and set(_producer_labels(closing)) & set(CERTIFICATE_KINDS)
    later = _later_unlabelled(closing_date)
    if completed:
        # A certificate means THAT sale completed, and what follows it is the clerk's proceeds
        # handling - "Disbursement of Sale Proceeds", "Surplus Funds from Sale" - which carries the
        # word and classifies as 'other'. Discarding every later entry went further than that
        # argument and suppressed a RESALE noticed after the certificate, which the seventh review
        # had already fixed for the phrasing classify does label (eighteenth review). The producer's
        # own date parser separates them: a proceeds entry prints no sale date after the certificate,
        # a rescheduled-sale notice does.
        later = [e for e in later
                 if any(d > closing_date for d in _sale_dates_of(e))
                 or _RESET_WORD_RE.search(_index_text(e))]
    if later:
        return 'unknown', ('%s, so whether a sale is pending cannot be told from this file'
                           % _unlabelled_phrase(later))
    return 'none', None


def _unlabelled_phrase(entries):
    """Says what is true of these entries: the producer gave them no sale label. It does NOT say
    they are unclassified - a certificate of sale is classified, and calling it unclassified told
    the human the opposite of what the producer saved."""
    return ('%d docket entr%s mention a sale that the classifier labelled neither a sale notice nor '
            'a cancellation (entr%s %s)' % (
                len(entries), 'y does' if len(entries) == 1 else 'ies do',
                'y' if len(entries) == 1 else 'ies',
                ', '.join(str(e.get('entry_id') or '?') for e in entries[:5])))


def _classify(text):
    """The label the PRODUCER's own classifier gives this text, or None. Never a label of our own."""
    if not str(text or '').strip():
        return None
    try:
        import miami_case_timeline
        return miami_case_timeline.classify(str(text))
    except Exception:                                  # noqa: BLE001 - a missing parser is not a verdict
        return None


def _replaces(entry):
    """True when this entry's own docket words say it REPLACES an earlier judgment.

    miami_case_timeline._REPLACES is the same regex reconcile_judgments uses at :674 to give a
    final_judgment row role='replacement', so nothing is classified here that the producer does not
    classify the same way.
    """
    text = _index_text(entry)
    if not text.strip():
        return False
    try:
        import miami_case_timeline
        return bool(miami_case_timeline._REPLACES.search(text))
    except Exception:                                  # noqa: BLE001 - a missing parser is not a verdict
        return False


def _cover_subject(entry):
    """-> the producer's own label for what a COVER-titled entry is about, or None.

    attached_document_kind exists only when the producer OPENED the document (:353). When it did not -
    the ordinary case for a login-walled filing - classify falls back to a cover label, and that label
    names the envelope: notice_of_filing, certificate_of_service, affidavit. So the unread half of the
    covering-title defect reached no check at all, and the docket where LESS was known read `supported`
    while the read half read incomplete (twenty-first review). This strips the cover head with the
    producer's own _FILED_ABOUT_RE and re-runs the producer's own classifier on the rest: no new
    classification, just the producer's two functions composed the way the producer composes them when
    it does have the document.
    """
    text = _index_text(entry).strip()
    if not text:
        return None
    try:
        import miami_case_timeline
        match = miami_case_timeline._FILED_ABOUT_RE.match(text)
    except Exception:                                  # noqa: BLE001 - a missing parser is not a verdict
        return None
    return _classify(text[match.end():]) if match else None


def _sale_dates_of(entry):
    """The sale dates this entry prints, off the producer's own saved field and its own parser.

    `sale_passages` is what build_timeline (:393, :397) saved for this entry: the docket line plus
    every body line of a READ page matching its sale vocabulary, and it is the field _transition :262
    takes the status's own sale_date from. Reading only the docket words gave the producer's parser a
    narrower input than the producer gave it, so a resale whose new date is printed in the document
    rather than in the docket line produced no date here - and the completed-sale filter below then
    discarded it and the case read `supported` over a resale after the certificate (nineteenth
    review). The field was saved by the producer and read by nothing in the repo.
    """
    passages = [str(p) for p in _rows(entry, 'sale_passages') if str(p or '').strip()]
    if passages:
        try:
            import miami_case_timeline
            return [d for d in (miami_case_timeline._sale_dates(passages) or []) if d]
        except Exception:                              # noqa: BLE001 - a missing parser is not a verdict
            return []
    text = _index_text(entry)
    if not text.strip():
        return []
    try:
        import miami_case_timeline
        return [d for d in (miami_case_timeline._sale_dates([text]) or []) if d]
    except Exception:                                  # noqa: BLE001 - a missing parser is not a verdict
        return []


def _newer(current, entry):
    if current is None:
        return entry
    return entry if str(entry.get('date') or '') >= str(current.get('date') or '') else current




def _bankruptcy_entries(timeline):
    """Bankruptcy filings on the docket that the stay history never took in.

    PER ENTRY, not "the history is empty". build_timeline skips an entry with no date, or a date
    after `as_of`, before building stay_history - and a fixed as_of is how the acceptance replay
    runs. Asking only whether stay_history was empty let a docket with an EARLIER bankruptcy absorb a
    second petition silently: relief granted in March, a fresh petition dated after the cutoff,
    verdict 'supported', stay column "no".
    """
    seen = {str(h.get('entry_id')) for h in _rows(timeline, 'stay_history') if isinstance(h, dict)}
    return [e for e in _rows(timeline, 'entries')
            if isinstance(e, dict) and set(_producer_labels(e)) & set(BANKRUPTCY_KINDS)
            and str(e.get('entry_id')) not in seen]


def _sale_day_bankruptcy(timeline):
    """-> the sale_held row when a bankruptcy entry landed on the sale day, else None.

    miami_case_timeline.sale_held (:553) records bankruptcy_order 'unresolved' and says in its own
    qualification that "whether the petition preceded the sale decides whether the sale is void
    under the automatic stay". If relief was later granted, stay_in_effect is False, so this fact
    reached the report through no other path and the one thing that decides whether the foreclosure
    sale stands was absent from every verdict.
    """
    held = timeline.get('sale_held')
    if isinstance(held, dict) and (held.get('bankruptcy_order') == 'unresolved'
                                  or held.get('bankruptcy_same_day')):
        return held
    return None


def _judgment_amount(timeline, entry_id):
    """-> (verified, failed, rejected) checks for the controlling judgment.

    A check counts for this judgment only when it NAMES that entry and was read off a COURT
    document. Both halves are load-bearing, and the first version of this function got both wrong:

      - It accepted `entry_id: None` as "could be this judgment". `run_case_timeline.load_rows` sets
        `entry_ref` only for a `court:` source_ref, so every recorded-instrument row in the same
        pipeline carries None. A recorded mortgage's principal could then corroborate a judgment
        amount, which is verify-12 defect D5 exactly - the corroboration document_prioritizer was
        changed to refuse.
      - It used `check.get('ok')` as a truthiness test, so `ok: 1` or the string `'false'` from
        hand-edited or older saved JSON read as a to-the-cent verification.

    A check that names this judgment but cannot be trusted is `rejected` and reported, never
    silently dropped: a discarded check is indistinguishable from no check at all.
    """
    verified, failed, rejected = [], [], []
    for check in _amount_checks(timeline):
        if entry_id is None or check.get('entry_id') != entry_id:
            continue
        source = str(check.get('source_ref') or '')
        if not source.startswith('court:'):
            rejected.append('a check for this judgment was read off %s, not the court copy'
                            % (source or 'a document with no source recorded'))
            continue
        if check.get('ok') is True:
            # `amount` must be a NUMBER, not merely present: a string amount from an older saved
            # format passed the None test, so `verified` was non-empty while the figure set was
            # empty - a row reading "supported" with "not verified" in the Amount column.
            if isinstance(check.get('amount'), bool) or not isinstance(
                    check.get('amount'), (int, float)):
                rejected.append('a check for this judgment verifies but records its amount as %r, '
                                'which is not a number' % (check.get('amount'),))
            else:
                verified.append(check)
        elif check.get('ok') is False:
            failed.append(check)
        else:
            rejected.append('a check for this judgment records ok=%r, which is neither true nor '
                            'false' % (check.get('ok'),))
    return verified, failed, rejected


def _coverage_of(timeline, entry_id):
    """-> (coverage, rows for this entry). `coverage` may be any shape; callers must not assume."""
    coverage = timeline.get('coverage')
    if not isinstance(coverage, dict):
        coverage = {}
    attachments = coverage.get('attachments')
    rows = [r for r in attachments if isinstance(r, dict)] if isinstance(attachments, list) else []
    return coverage, [r for r in rows if r.get('entry_id') == entry_id]


def _state_counts(rows):
    counts = {}
    for row in rows:
        state = row.get('state')
        if state:
            counts[state] = counts.get(state, 0) + 1
    return counts


def assess(timeline, dossier=None):
    """-> the verdict dict for one case. Pure; takes the saved timeline (and optionally its dossier).

    EVERY REQUIREMENT IS SHOWN, NOT ASSUMED. The first version of this function asked only whether
    anything contradicted "supported", so absent data produced it: a timeline with no coverage block,
    no coverage row for the controlling judgment, an unrecognised status kind, or `ok: 1` each read
    as supported. In this domain that is the one unacceptable direction, so each requirement below
    has to be positively satisfied by something saved, and anything else lands in `missing`.

    Every string in `conflicts`, `missing` and `supported_by` names where it came from, so a reader
    can go straight to the producing block instead of trusting this summary.
    """
    conflicts, missing, notes, supported_by = [], [], [], []
    status = timeline.get('status')
    if not isinstance(status, dict):
        status = {}
    kind = status.get('kind')
    entry_id, judgments = _controlling(timeline)

    # --- posture -------------------------------------------------------------------------------
    reason = str(judgments.get('controlling_reason') or '')
    if reason == CONFLICTING_JUDGMENTS:
        conflicts.append('judgments: %s' % reason)
    elif reason == NO_OPERATIVE_JUDGMENT:
        missing.append('judgments: %s' % reason)
    if kind in (None, 'unclear'):
        text = str(status.get('reason') or 'no reason recorded')
        (conflicts if text in CONFLICT_REASONS else missing).append('docket status unclear: %s' % text)
    elif kind == 'active_pre_judgment':
        missing.append('no judgment entered yet')
    elif kind not in SETTLED_KINDS:
        # An allowlist, not a denylist. miami_case_timeline owns this vocabulary; if it gains a kind
        # or renames one, every case must read incomplete until someone looks, rather than every
        # case silently reading supported.
        missing.append('docket status %r is not a posture this module can vouch for' % (kind,))
    if entry_id is None and reason not in (CONFLICTING_JUDGMENTS, NO_OPERATIVE_JUDGMENT):
        missing.append('no single controlling judgment')
    # reconcile_judgments (:681) reaches ONE operative judgment by inferring that an image-less or
    # login-walled same-day entry with the same docket code is the same judgment listed twice - its
    # own reason says "(inferred, not read)". The uniqueness of the controlling judgment is exactly
    # what "supported" is scoped to, so it cannot rest on a document nobody opened.
    duplicates = [d for d in (judgments.get('docket_duplicates_inferred') or []) if d]
    if duplicates and entry_id is not None:
        # Why the twin went unread decides whether this is a gap or a note, and the producer says
        # which in the duplicate row's own reason (miami_case_timeline :686, :690):
        #   behind the county login -> a document EXISTS and nobody read it. The uniqueness of the
        #     controlling judgment, which is what "supported" is scoped to, rests on an unread
        #     filing, so it holds the case.
        #   no document image -> the county indexes no document, so there is nothing to read and
        #     the inference rests on the docket index, which is what the whole reconciliation
        #     rests on. A note.
        # An unrecognised reason holds the case, the same policy as the 'unclear' reasons above.
        rows = judgments.get('judgments') if isinstance(judgments.get('judgments'), list) else []
        by_id = {str(r.get('entry_id')): r for r in rows if isinstance(r, dict)}
        for dup in duplicates:
            text = str((by_id.get(str(dup)) or {}).get('reason') or '')
            if text.startswith('no document image;'):
                notes.append('entry %s is taken as this judgment listed twice; the county indexes '
                             'no document for it, so the inference rests on the docket index' % dup)
            else:
                missing.append('one operative judgment only because same-day final judgment entry '
                               '%s was inferred to be a duplicate without being read%s'
                               % (dup, ' (%s)' % text if text else
                                  ' and no reason for the inference was saved'))
    # A scheduled sale date that has passed with no certificate: the producer's own reason says
    # "whether a sale occurred is unknown". That is not a posture to vouch for, whatever the kind.
    outcome = status.get('sale_outcome')
    if outcome in UNSETTLED_SALE_OUTCOMES:
        missing.append('the sale date has passed and the outcome is %s' % outcome)
    # The same fact reaches the status only through one branch: build_timeline :509 writes
    # sale_outcome inside `if status['kind'] == 'sale_scheduled' and status.get('sale_date') and
    # status['sale_date'] < today`. A notice of sale whose docket words carry no parseable date
    # leaves sale_date None, so that branch never runs - and the top-level block was read only under
    # a stay, by _sale_state. The clerk's money rows said a sale was HELD and the verdict was
    # `supported` for "sale scheduled" (eighth review). The producer's own qualification on this
    # block ends "the sale can still be vacated", which is not a posture to vouch for.
    held = timeline.get('sale_held') if isinstance(timeline.get('sale_held'), dict) else {}
    money = _labelled(timeline, SALE_MONEY_KINDS)
    cert_at_cutoff, late_cert = _certificate_at_cutoff(timeline, held)
    certificates = [c for c in _labelled(timeline, CERTIFICATE_KINDS, since=held.get('date'))
                    if not _after_cutoff(c, timeline.get('as_of'))]
    # Only a CERTIFICATE closes a held sale. An order cancelling a sale filed after the clerk posted
    # bids says nothing about whether that sale was held, and SALE_CLOSING_KINDS carries one.
    closing_for_money = _labelled(timeline, CERTIFICATE_KINDS)
    if money and not held.get('date') and not [
            c for c in closing_for_money
            if str(c.get('date') or '') >= max(str(e.get('date') or '') for e in money)]:
        # The summary is empty while the entries carry the labels it is built from: the :383 override
        # emptied it, and the evidence that a sale was held is in the file (ninth review).
        missing.append("entr%s %s carr%s the clerk's sale-day bid or deposit label while the run's "
                       'own held-sale summary took none of them in, so whether a sale was held is '
                       'not settled in this file'
                       % ('y' if len(money) == 1 else 'ies',
                          ', '.join(str(e.get('entry_id') or '?') for e in money[:5]),
                          'ies' if len(money) == 1 else 'y'))
    elif held.get('date') and not cert_at_cutoff and outcome not in UNSETTLED_SALE_OUTCOMES:
        if late_cert is not None:
            # The summary took this certificate in, so the "did not take in" wording below would be
            # false; what is true is that it is dated outside the window this run reports on.
            missing.append("the clerk's sale-day bid and deposit entries say a sale was held on %s "
                           'and the only certificate the run found for it is entry %s, dated after '
                           "this run's as_of, so what became of the sale at the cutoff is not in "
                           'this file' % (held['date'], late_cert))
        elif certificates:
            missing.append("the run's held-sale summary for %s records no certificate while entr%s "
                           '%s carr%s a certificate label it did not take in, so what became of the '
                           'sale is not settled in this file'
                           % (held['date'], 'y' if len(certificates) == 1 else 'ies',
                              ', '.join(str(e.get('entry_id') or '?') for e in certificates[:5]),
                              'ies' if len(certificates) == 1 else 'y'))
        else:
            missing.append("the clerk's sale-day bid and deposit entries say a sale was held on %s "
                           'and no certificate of sale has followed, so what became of it is not in '
                           'this file' % held['date'])
    record = _judgment_record(judgments, entry_id)
    if entry_id is not None and record is None:
        missing.append('no record for the controlling judgment in the reconciliation, so nothing '
                       'says what became of it')
    elif record is not None:
        if record.get('status') != 'operative':
            missing.append('the controlling judgment is %s: %s'
                           % (record.get('status') or 'in an unrecorded state',
                              str(record.get('reason') or 'no reason recorded')))
        if str(record.get('satisfaction') or 'no_satisfaction_found') != 'no_satisfaction_found':
            missing.append('the controlling judgment is %s; no satisfaction found is not proof of '
                           'an open balance, and a partial one is not proof of scope'
                           % record.get('satisfaction'))

    # --- the bankruptcy stay -------------------------------------------------------------------
    stay = timeline.get('stay_in_effect')
    history = _rows(timeline, 'stay_history')
    # _sale_state is read for EVERY case. It used to be read only inside `if stay is True`, so on
    # every other docket the producer's own labels could say a sale was still running and the verdict
    # threw that away: a sale noticed for 2026-04-01, an as_of in September, nothing on the docket
    # cancelling it and no certificate, read `supported` (sixteenth review). Nothing else covers it -
    # sale_outcome is written only while the FINAL status is 'sale_scheduled' with a parsed sale date
    # (miami_case_timeline :508), which a later judgment entry moves off, and the held-sale block
    # needs the clerk's sale-day money rows, which a sale nobody has held yet does not have.
    state, sale = _sale_state(timeline, status, kind)
    if stay is True:
        # A well-evidenced stay is not a contradiction, and the status table's 2023-020247 is
        # "supported" with one in effect. A stay in effect while the docket runs a sale IS one:
        # 2018-026274, stay #93 with no relief order, against an amended judgment and a sale notice
        # for the same month. Where the file cannot say which of those it is, that is a gap - not a
        # contradiction and not a clean bill.
        if state == 'live':
            conflicts.append('a bankruptcy stay is in effect while the docket shows a sale going '
                             'ahead (%s); no relief order was identified' % sale)
        elif state == 'unknown':
            missing.append('a bankruptcy stay is in effect and %s' % sale)
        else:
            notes.append('a bankruptcy stay is in effect; nothing here clears anyone to be '
                         'contacted')
    elif stay is None and history:
        missing.append('stay state unknown')
    if stay is not True and (state == 'unknown'
                             or (state == 'live' and kind == 'judgment_entered')):
        # Two different scopings, because the producer's status is evidence about one of these states
        # and not the other.
        #
        # 'live' is reported only on 'judgment_entered'. The other settled kinds mean the entry that
        # ended the case is newer than the sale entries, because every kind `live` is built from -
        # notice_of_sale, order_resetting_sale, a certificate, a cancellation - IS a _transition the
        # status loop would have taken. _sale_state knows nothing of dismissals or satisfactions, so
        # it calls those dockets 'live' too, and reporting them would hold four routine shapes
        # incomplete for good.
        #
        # 'unknown' is reported on every kind, because that argument cannot reach the entries it is
        # built from. classify leaves "Notice of Rescheduled Foreclosure Sale" and "Notice of
        # Cancellation of Foreclosure Sale" as 'other', _transition has no entry for 'other', so
        # those entries produce no transition and are INVISIBLE to the status loop - they can be
        # arbitrarily newer than whatever set the settled kind. A cancellation order between the
        # notice and the rescheduling flipped the identical evidence from incomplete to supported
        # (seventeenth review). The completed-sale case is excluded inside _sale_state, where the
        # producer's own certificate label answers it.
        missing.append('the docket status is %r while %s' % (kind, sale))
    if kind == 'sale_scheduled' and not status.get('sale_date'):
        # The eighth review's defect, other half. _transition (:263) takes the sale date from
        # sale_passages - the docket line plus the body lines of READ pages - and falls back to the
        # entry's own date only for a calendar event (:264). A notice of sale whose description
        # carries no parseable date and whose document is behind the county login therefore leaves
        # sale_date None, and build_timeline's past-sale check (:508) is written
        # `if kind == 'sale_scheduled' and status.get('sale_date') and ... < today`, so it never runs
        # and no sale_outcome is saved. The case with LESS known about it was the one reading
        # `supported`: the same docket with the date printed in the description is incomplete.
        missing.append("the docket status is 'sale_scheduled' and the run parsed no sale date, so "
                       "miami_case_timeline's own past-sale check never ran and whether that sale "
                       'has been held is not in this file')
    unseen = _bankruptcy_entries(timeline)
    if stay is not True and unseen:
        # On the docket but never in stay_history: undated, or dated after the run's as_of. Either
        # way the stay state is unknown, not absent - whether or not an EARLIER bankruptcy did reach
        # the history.
        missing.append('a bankruptcy filing is on the docket (entr%s %s) that the stay history '
                       'never took in, so the stay state is unknown rather than settled'
                       % ('y' if len(unseen) == 1 else 'ies',
                          ', '.join(str(e.get('entry_id') or '?') for e in unseen[:5])))
    # Not named `held`: the held-sale block further down binds that to timeline['sale_held'], and
    # rebinding it here was a trap for the next edit in either block.
    sale_day = _sale_day_bankruptcy(timeline)
    if sale_day:
        conflicts.append('a bankruptcy entry (%s) landed on the day of the sale (%s); whether the '
                         'petition preceded the sale decides whether the sale is void under the '
                         'automatic stay, and the docket gives dates, not times'
                         % (', '.join(str(e) for e in (sale_day.get('bankruptcy_same_day') or ['?'])),
                            sale_day.get('date') or 'unknown date'))

    # `mine` is the controlling judgment's own coverage rows; the amount block below prints
    # which documents on the entry were read, so it is read before both sections.
    coverage, mine = _coverage_of(timeline, entry_id)
    # --- the amount ----------------------------------------------------------------------------
    verified, failed, rejected = _judgment_amount(timeline, entry_id)
    missing.extend(rejected)
    for check in failed:
        why = str(check.get('reason') or '')
        # A printed subtotal its own rows do not reproduce is the document disagreeing with itself.
        # The status table calls this "amount incomplete"; it is recorded here as a contradiction
        # too, because a figure that cannot be made to add up is not merely unread.
        if check.get('disagreeing_subtotals'):
            # Reachable from miami_judgment's text path (saved as sum_check_disagreeing_subtotals),
            # not from the timeline's amount_checks: verify_document builds its rows with
            # vision_rows, which marks every row `explicit`, and for an explicit row
            # _resolve_subtotal either resolves or raises - it never returns None, so
            # _structural_fixups never writes the notes disagreeing_subtotals is collected from.
            # Kept as the reader for the shape when it is present, never claimed as a live signal.
            conflicts.append("the judgment's own printed subtotal does not equal its printed rows: "
                             '%s' % _subtotal_gap(check))
        elif why == SUBTOTAL_DISAGREES:
            # This is what 2018-026274's $0.60 actually looks like in a saved timeline. The one
            # reason string covers two different things - members that could not be read, and
            # members read fine that do not add up to the printed subtotal - and judgment_money
            # raises it for both, so which one this is cannot be told from the file. Reported as a
            # gap, the side that cannot overstate what is known, with the ambiguity named.
            missing.append('a printed subtotal on the judgment does not agree with its own listed '
                           'items, or those items could not be read; judgment_money reports both '
                           'as "%s", so the file does not say which' % why)
        elif why == AMBIGUOUS_RUN:
            conflicts.append('two different runs of printed rows both reach %s; the document '
                             'supports more than one reading' % _money(check.get('amount')))
        elif why == TOTAL_ROW_DISAGREES:
            # One reason string, several different facts: judgment_money raises it at :486 both when
            # NO printed total row matches the stated total and when one matches but a second
            # disagreeing kind=total row sits on the same page (`if not candidates or others`), and
            # again at :348, :384 and :388 for further cases. Saying only the first was a sentence
            # the document refutes, so the ambiguity is named the way SUBTOTAL_DISAGREES does below.
            missing.append('the printed total rows on the judgment page do not settle %s: either '
                           'none of them matches it or more than one disagrees, and which of those '
                           'it is cannot be told from this file (%s)'
                           % (_money(check.get('amount')), why))
        elif 'disagreeing_subtotals' in check:  # noqa: SIM114 - the reason is the point, see below
            # run_case_timeline always writes the key now, so every OTHER failure reason
            # judgment_money emits landed here and produced no line at all: the verdict fell to
            # incomplete on the generic fallback below, with nothing saying why.
            missing.append('a total for the controlling judgment does not verify: %s'
                           % (why or 'no reason recorded'))
        else:
            # run_case_timeline started saving that key in the commit that added this module, and
            # keep_cached_amounts (:258) copies an older check verbatim for any document a later
            # pass does not re-read. So on evidence saved before then, the difference between "the
            # pages were not all read" and "the document cannot add up" - 2018-026274's $0.60 - is
            # not in the file at all. Say that, rather than let it degrade silently to "not read".
            # A check saved before run_case_timeline started keeping disagreeing_subtotals: the
            # difference between "the pages were not all read" and "the document cannot add up" -
            # 2018-026274's $0.60 - is not in the file at all.
            # An older saved row, from before run_case_timeline kept the key at all. Re-running
            # does not recover a subtotal breakdown either (see the note above), so this says only
            # what is true: the check is older than the current saved shape.
            missing.append('a total for the controlling judgment does not verify (%s), on a check '
                           'saved in an older shape than this run writes' % why)
    amounts = sorted({round(float(c['amount']), 2) for c in verified
                      if isinstance(c.get('amount'), (int, float))})
    if len(amounts) > 1:
        # verify_document returns one check per stated grand total (judgment_money :457) and
        # document_vision appends one grand total PER PAGE (:263), so a judgment printing a
        # different total on two pages yields two checks that both verify. Two verified figures
        # that are not the same number is the document disagreeing with itself, and the first
        # version reported it as "amount verified to the cent" without printing either figure.
        conflicts.append('two different totals on the controlling judgment each verify to the '
                         'cent: %s' % ', '.join(_money(a) for a in amounts))
    if verified:
        # Blocking on this held routine dockets and flipped a pilot case, so it is a note: nothing
        # saved says which attachment on an entry IS the judgment, and the standing qualification
        # cannot say WHICH cases that bites on. Without the note a reader sees a confident figure
        # that may be a sibling document's total - an Affidavit of Indebtedness filed under the same
        # entry has its own (fourteenth review).
        read_docs = {str(r.get('document') or '') for r in mine if r.get('state') == 'read'}
        read_docs.discard('')
        if len(read_docs) > 1:
            notes.append("the judgment's docket entry carries %d read documents (%s); the figure "
                         'verified on %s, and nothing saved says which of them is the judgment'
                         % (len(read_docs), ', '.join(sorted(read_docs)),
                            ', '.join(sorted({str(c.get('source_ref') or '?') for c in verified}))))
        supported_by.append('amount %s verified to the cent on %s' % (
            ', '.join(_money(a) for a in amounts) or 'recorded with no figure',
            ', '.join(sorted({str(c.get('source_ref') or c.get('pages') or '?') for c in verified}))))
    else:
        missing.append('no total for the controlling judgment verifies to the cent'
                       + (' (%d check(s) failed)' % len(failed) if failed else ''))
    if verified and failed:
        # A judgment can print several totals (verify_document returns one check per grand total).
        # One verifying does not excuse another that the document cannot reproduce, and reporting
        # only the good one is how "the amount verified" gets said about a document that disagrees
        # with itself.
        missing.append('%d other total(s) on the controlling judgment do not verify: %s'
                       % (len(failed), '; '.join(sorted(
                           '%s (%s)' % (_money(c.get('amount')), c.get('reason') or 'no reason recorded')
                           for c in failed))))

    # --- what was read for THIS judgment -------------------------------------------------------
    if entry_id is not None and not mine:
        # Absence is not evidence of reading. A timeline saved before `coverage` existed, a partial
        # write, or an attachment list that simply has no row for this entry all land here.
        missing.append("no coverage row for the controlling judgment, so nothing shows its filing "
                       "was read")
    for row in mine:
        state = row.get('state')
        if state in LOGIN_WALLED:
            missing.append("the controlling judgment's filing is behind the clerk's login (%s)" % state)
        elif state in NO_IMAGE:
            missing.append("the controlling judgment's filing has no document to read (%s)" % state)
        elif state in NOT_REACHED:
            missing.append("the controlling judgment's filing has a document the run never reached "
                           '(%s)' % state)
        elif state in PART_READ:
            missing.append("the controlling judgment's filing was only partly read (%s)" % state)
        elif state != 'read':
            missing.append("the controlling judgment's filing is %s" % (state or 'in an unrecorded state'))

    # --- everything else, reported and never a blocker (see the module docstring) ---------------
    # Identity, not value: two attachments can be equal dicts, and excluding by == would then drop
    # a second row that is a different filing.
    others = [r for r in (coverage.get('attachments') or [])
              if isinstance(r, dict) and not any(r is m for m in mine)] if isinstance(
                  coverage.get('attachments'), list) else []
    counts = _state_counts(others)
    elsewhere = {k: v for k, v in counts.items() if k not in ('read', 'county_no_document')}
    if elsewhere:
        notes.append('elsewhere on the docket, attachments not read: '
                     + ', '.join('%s %d' % (k, v) for k, v in sorted(elsewhere.items())))
    walled = sum(v for k, v in counts.items() if k in LOGIN_WALLED)
    if walled:
        notes.append('%d filing(s) behind the clerk\'s login; a login is a decision, not a bug' % walled)
    # The timeline's OWN gaps (budget_exhausted, amount_page_unreadable,
    # inventory_completeness_unknown and the rest) reached the report only where they happened to
    # change a coverage state. They are the run saying what it could not do, so they are named.
    # Two fields the producer writes and, until the eleventh review, nothing in the repo read. Both
    # are one line here and both were a false `supported` with the amount vouched for "to the cent".
    #
    # attached_document_kind (:366): where a dispositive document is filed UNDER a covering title -
    # "Notice of Filing Satisfaction of Judgment", which _FILED_ABOUT_RE's bare `notice\b` matches -
    # the producer moves the real label here and leaves kind as the cover. Its rule is defensible: a
    # judgment on page 1 of a motion is an exhibit, not a new judgment. But then reconcile_judgments
    # and _transition never see it, so a satisfied, vacated, dismissed or sold case kept a status of
    # judgment_entered with an operative judgment and nothing in the report named the document.
    for entry in _rows(timeline, 'entries'):
        if not isinstance(entry, dict) or _after_cutoff(entry, timeline.get('as_of')):
            continue
        attached = entry.get('attached_document_kind')
        # final_judgment is deliberately excluded. It is in _DISPOSITIVE_BODIES, so a motion for
        # summary judgment, a proposed judgment, a memorandum or a status report carrying a judgment
        # copy all set this key - and holding on those made routine dockets incomplete for good
        # (twelfth review; miami_case_timeline :272 names three real pilot entries of this shape).
        # The producer's own rule is that such a copy is an exhibit, and a judgment body under a
        # covering title can only mean "a judgment exists", which reconcile_judgments already read.
        # The other five dispositive bodies genuinely move the posture.
        # ... unless the entry's own words say the document REPLACES a judgment. "Notice of Filing
        # Amended Final Judgment of Foreclosure" is not an exhibit: it is the thing that supersedes
        # the judgment whose amount this verdict vouches for, and the producer folds it in nowhere -
        # _transition has no entry for notice_of_filing, and reconcile_judgments keys on
        # kind == 'final_judgment' (:672), so the superseded judgment stays operative and controlling.
        # A case read `supported` with the OLD figure verified to the cent and the amendment named
        # nowhere on the page (twentieth review). The test is the producer's own _REPLACES.
        if attached is None and set(_producer_labels(entry)) & set(COVER_KINDS):
            # The unread half. The sale-calendar kinds are deliberately left out: they route through
            # _sale_state's unlabelled scan instead, which carries the live_floor and closing-word
            # guards three rounds were spent calibrating, and raising an unconditional gap for them
            # held the routine live-lead docket (an "Affidavit of Publication of Notice of
            # Foreclosure Sale" beside a live notice) incomplete.
            subject = _cover_subject(entry)
            if (subject in DECIDING_KINDS
                    and subject not in SALE_NOTICE_KINDS + ('order_cancelling_sale',)
                    and (subject != 'final_judgment' or _replaces(entry))):
                attached = subject
        if attached in DECIDING_KINDS and (attached != 'final_judgment' or _replaces(entry)):
            missing.append('entry %s is titled as a filing about something else, and the document '
                           'under it reads as %s, which the run therefore did not fold into the '
                           "case's posture; whether it decides this case is not settled in this file"
                           % (entry.get('entry_id') or '?', attached))
    # An entry the producer LABELLED a posture-deciding kind and could not DATE. build_timeline has
    # one net for these - :505 forces status 'unclear' for every undated entry `_transition`
    # recognises - and `_transition` returns None for exactly the kinds that then reach nothing else:
    # sale_bid and sale_deposit are not in its map at all (:250), and a limited-scope satisfaction or
    # dismissal is returned None at :248. reconcile_judgments (:673) skips every undated entry, so an
    # undated satisfaction never reaches the judgment row either, and _labelled drops undated rows
    # because every producer summary it mirrors does. That left no check at all: an undated "Bid
    # Amount" over a read judgment, and an undated partial satisfaction OF that judgment, both read
    # `supported` with missing empty (fifteenth review). This is the policy _after_cutoff already
    # states - an entry with no date is more unknown, not less.
    for entry in _rows(timeline, 'entries'):
        if not isinstance(entry, dict) or entry.get('date'):
            continue
        lost = sorted(set(_producer_labels(entry)) & set(DECIDING_KINDS))
        if lost:
            missing.append('entry %s carries the %s label and the run could not date it, so every '
                           'summary built from the docket chronology left it out; what it decides is '
                           'not settled in this file'
                           % (entry.get('entry_id') or '?', ', '.join(lost)))
    # limited_scope / dismissed_parties (:367) on a DISMISSAL. _transition returns None for a
    # limited-scope dismissal (:234), so the status never moves - and unlike a limited-scope
    # satisfaction, which reconcile_judgments records as partially_satisfied and which this module
    # already reads, nothing reconciles a dismissal. A whole action voluntarily dismissed read
    # `supported` with status judgment_entered (twelfth review). scope_of (:569) sets limited off a
    # bare `\bonly\b` near `dismiss`, with no party named, which is reported and not changed.
    for entry in _rows(timeline, 'entries'):
        if (not isinstance(entry, dict) or not entry.get('limited_scope')
                or _after_cutoff(entry, timeline.get('as_of'))):
            continue
        if not set(_producer_labels(entry)) & {'notice_of_voluntary_dismissal', 'order_of_dismissal'}:
            continue
        parties = [str(x) for x in _rows(entry, 'dismissed_parties')]
        missing.append('entry %s is a dismissal the run read as limited in scope%s, so it did not '
                       "move the case's posture; what it dismisses is not settled in this file"
                       % (entry.get('entry_id') or '?',
                          ' (as to %s)' % ', '.join(parties[:5]) if parties else
                          ' and it names no party'))
    # unmatched (:756): reconcile_judgments' own list of dispositive events it could not link to a
    # judgment. A satisfaction citing the mortgage's recording date rather than the judgment's - the
    # ordinary shape - lands here (_target, :793), and an unmatched satisfaction left the judgment
    # `operative` / `no_satisfaction_found`. An unmatched VACATUR is already safe: :721 marks every
    # candidate unclear, so the case conflicts. Only satisfaction leaked.
    for event in _rows(judgments, 'unmatched'):
        if not isinstance(event, dict):
            continue
        missing.append('the reconciliation could not link entry %s, which it reads as %s, to any '
                       'judgment (%s), so what it acts on is not settled in this file'
                       % (event.get('entry_id') or '?', event.get('kind') or 'a dispositive event',
                          str(event.get('reason') or 'no reason recorded')))
    # The relabel sweep. Deliberately last among the gap checks and deliberately not per-summary:
    # every one of the ten rounds' label defects was this one relabel reaching a summary nobody had
    # enumerated yet, so the entry is named once and the case is held, whatever consumed it.
    for entry, lost in _relabelled(timeline):
        missing.append('entry %s is a calendar event, so the run relabelled it "hearing" and the '
                       'summaries built from that label did not take it in (%s); what it decides is '
                       'not settled in this file' % (entry.get('entry_id') or '?', lost))
    kinds = sorted({str(g.get('kind') or g.get('reason') or '?').split(':')[0]
                    for g in _rows(timeline, 'gaps') if isinstance(g, dict)})
    if kinds:
        notes.append('the run recorded gaps of its own: ' + ', '.join(kinds))
    if isinstance(dossier, dict):
        gaps = _rows(dossier, 'open_gaps')
        for gap in gaps[:20]:
            notes.append('dossier: %s' % gap)
        if len(gaps) > 20:
            notes.append('dossier: %d more open gap(s) not listed here' % (len(gaps) - 20))

    verdict = 'conflicted' if conflicts else ('incomplete' if missing else 'supported')
    return {'case': timeline.get('case'), 'as_of': timeline.get('as_of'),
            'verdict': verdict,
            'controlling_judgment': entry_id,
            # The figure itself, not just "verified": the status table's whole point is the amount,
            # and a report that cannot print it cannot replace the hand-written column. None when
            # nothing verifies; a list when the document verifies two different totals.
            'judgment_amount': amounts[0] if len(amounts) == 1 else (amounts or None),
            'docket_status': kind,
            'stay_in_effect': stay, 'stay_history_count': len(history),
            # So the report can never print "none on the docket" over a docket that has one.
            'bankruptcy_entry_count': len(unseen),
            'conflicts': conflicts, 'missing': missing, 'notes': notes,
            'supported_by': supported_by,
            'caveat': CAVEAT, 'qualification': QUALIFICATION}


def _subtotal_gap(check):
    """judgment_money's disagreeing_subtotals, in a sentence a person reads.

    `difference` is signed (rows above minus the printed subtotal, so 2018-026274's is -0.60), and
    `_nearest_rows_above` omits both `rows_above_sum` and `difference` when the row above the
    subtotal is not additive. Printing the raw values gave "off by $-0.60" and "vs None above it".
    """
    out = []
    for row in (check.get('disagreeing_subtotals') or []):
        if not isinstance(row, dict):
            continue
        label = row.get('label') or 'subtotal'
        total = row.get('rows_above_sum')
        if total is None:
            out.append('%s %s, with no additive rows above it to compare'
                       % (label, _money(row.get('amount'))))
            continue
        gap = row.get('difference')
        out.append('%s %s vs %s in the rows above it%s' % (
            label, _money(row.get('amount')), _money(total),
            (' (off by %s)' % _money(abs(gap))) if isinstance(gap, (int, float)) else ''))
    return '; '.join(out) or 'no detail recorded'


def _money(value):
    # Thousands separators, because the figure this report exists to carry is the status table's
    # "$1,746,032.70" and a reader compares it against the judgment by eye.
    try:
        return '${:,.2f}'.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _amount_word(value):
    # The column the status table is for. A list means the document verified two different totals,
    # which is a conflict the row's Why already names; both figures still print.
    if isinstance(value, list):
        return ' / '.join(_money(v) for v in value) or 'not verified'
    return _money(value) if isinstance(value, (int, float)) else 'not verified'


def _stay_word(value, history=None, on_docket=None):
    # miami_case_timeline leaves stay_in_effect None when there is no bankruptcy HISTORY, which is
    # not the same as no bankruptcy on the docket: it skips an undated entry, and one dated after the
    # run's as_of, before building that history. Saying "none on the docket" on those was a positive
    # claim the entries refuted, and on one of them the verdict read 'supported' as well.
    # `on_docket` counts bankruptcy entries the history never took in. With one of those, the stay
    # state is unknown whatever stay_in_effect says: False there means "the history's last event
    # closed it", not "no live petition".
    if on_docket:
        return 'IN EFFECT' if value is True else 'unknown'
    if value is None:
        return 'unknown' if history else 'none on the docket'
    return {True: 'IN EFFECT', False: 'no'}.get(value, str(value))


def render_markdown(rows, skipped=(), broken=()):
    # The stay column is not decoration. The first version of this report rendered only
    # conflicts/missing/supported_by, so 2023-020247 - supported, with a bankruptcy stay in effect -
    # printed as a clean row with nothing anywhere on the page saying a stay was in force.
    # The posture column is not decoration either, for the same reason. A dismissed, sold or
    # sale-cancelled case is `supported` under this module's scope - the producer folds none of those
    # into the judgment row or the amount - so all three printed as a clean row with a judgment
    # amount beside them and nothing on the page saying the foreclosure was over. A `sold` case means
    # a third party holds the certificate of title (twentieth review).
    as_of = sorted({str(r.get('as_of') or '') for r in rows} - {''})
    out = ['# Case verdicts', '']
    if as_of:
        # Several gap strings say "this run's as_of", and a replay at an old cutoff otherwise reads
        # exactly like a run made today.
        out += ['Evidence as of %s.' % ', '.join(as_of), '']
    out += ['| Case | Verdict | Posture | Judgment | Amount | Bankruptcy stay | Why |',
            '|---|---|---|---|---|---|---|']
    for row in sorted(rows, key=lambda r: (REVIEW_ORDER.index(r['verdict']), str(r.get('case')))):
        why = (row['conflicts'] + row['missing'] + row['supported_by']) or ['-']
        # Three reasons fit a table cell; the rest must still be counted. This is the human-facing
        # report, and dropping evidence that says no is the one thing it must not do.
        shown = '; '.join(w.replace('|', '/') for w in why[:3])
        if len(why) > 3:
            shown += '; +%d more, in the JSON' % (len(why) - 3)
        out.append('| %s | %s | %s | %s | %s | %s | %s |' % (
            row.get('case') or '?', row['verdict'], row.get('docket_status') or '?',
            row.get('controlling_judgment') or '-',
            _amount_word(row.get('judgment_amount')),
            _stay_word(row.get('stay_in_effect'), row.get('stay_history_count'),
                       row.get('bankruptcy_entry_count')),
            shown))
    noted = [r for r in rows if r.get('notes')]
    if noted:
        out += ['', '## Also on the record', '']
        for row in sorted(noted, key=lambda r: str(r.get('case'))):
            out.append('- **%s**' % (row.get('case') or '?'))
            for note in row['notes']:
                out.append('  - %s' % note)
    # A case that produced no verdict is part of the report. Printing it only to stdout made a
    # case whose timeline never saved indistinguishable, in the file, from one never in scope.
    if skipped or broken:
        out += ['', '## No verdict', '']
        for path in sorted(skipped):
            out.append('- no whole-case timeline saved: %s' % path)
        for note in sorted(broken):
            out.append('- saved evidence does not load or does not parse: %s' % note)
    out += ['', '## Counted', '']
    for name in REVIEW_ORDER:
        out.append('- %s: %d' % (name, sum(1 for r in rows if r['verdict'] == name)))
    out.append('- no verdict: %d' % (len(skipped) + len(broken)))
    out += ['', CAVEAT, '', QUALIFICATION, '']
    return '\n'.join(out)


class Unreadable(Exception):
    """The file is there and will not parse. Not the same as a file nobody ever wrote."""


def _load(path, required=False):
    """-> the parsed JSON, or None when the file is absent.

    A partial write is the real failure this module has to survive, and swallowing ValueError filed
    it under "no whole-case timeline saved" - in the report, indistinguishable from a case that was
    never run.
    """
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError:
        return None
    try:
        return json.loads(text)
    except ValueError as exc:
        if required:
            raise Unreadable('%s: %s' % (Path(path).name, exc))
        return None


def for_saved_case(dossier_path):
    """-> the verdict for one saved dossier, or None when it has no whole-case timeline yet."""
    path = Path(dossier_path)
    saved = path.with_name(path.stem + '-timeline.json')
    timeline = _load(saved, required=True)
    if timeline is None and not saved.exists():
        return None
    if not isinstance(timeline, dict):
        # Parsing is not the only way a file can be wrong. `null` or a bare list parses fine and is
        # not a timeline; filing it under "no whole-case timeline saved" puts a corrupt file in the
        # bucket _load's docstring says it must never land in - indistinguishable from never run.
        raise Unreadable('%s: %s' % (saved.name, 'could not be read'
                                     if timeline is None else
                                     'parsed to %s, not a timeline object'
                                     % type(timeline).__name__))
    # The dossier contributes NOTES only (its open_gaps); the verdict rests entirely on the timeline.
    # Requiring it discarded a whole readable verdict over a truncated file that changes nothing
    # about it (ninth review). A dossier that is there and will not parse is reported as a gap on the
    # case, which is the honest form: something the run wrote cannot be read.
    try:
        dossier = _load(path, required=True)
    except Unreadable as exc:
        row = assess(timeline, None)
        row['missing'] = list(row['missing']) + [
            'the saved dossier for this case does not parse (%s), so its own open gaps are not in '
            'this verdict' % exc]
        row['verdict'] = 'conflicted' if row['conflicts'] else 'incomplete'
        return row
    return assess(timeline, dossier if isinstance(dossier, dict) else None)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Emit the supported / incomplete / conflicted verdict for saved Miami cases. '
                    'Reads saved evidence only: no requests, no spending.')
    parser.add_argument('--dossiers', help='directory of saved <case>.json dossiers')
    parser.add_argument('--case', action='append', default=[],
                        help='one saved dossier path; repeatable')
    parser.add_argument('--out', help='write the report here (default: alongside the dossiers)')
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.case]
    if args.dossiers:
        # This module's own output lands in the same directory, so a rerun would read
        # case-verdicts.json as a dossier and print it as skipped every time. The default names are
        # not enough: --out takes any name, and `--dossiers D --out D/report.json` made the next run
        # read its own report forever (ninth review). Exclude the name this run will actually write.
        mine = {'case-verdicts'}
        if args.out:
            mine.add(Path(args.out).stem)
        paths += sorted(p for p in Path(args.dossiers).glob('*.json')
                        if not p.stem.endswith('-timeline')
                        and not any(p.stem == m or p.stem.startswith(m + '-') for m in mine))
    if not paths:
        parser.error('give --dossiers DIR or --case FILE')

    rows, skipped, broken = [], [], []
    for path in paths:
        # An unattended report must survive one malformed file. Before this, a saved timeline whose
        # `judgments` was a list raised mid-assess and no report was written at all, so the good
        # cases' verdicts were lost with the bad one.
        try:
            row = for_saved_case(path)
        except Exception as exc:                      # noqa: BLE001 - named in the report, never swallowed
            broken.append('%s: %s: %s' % (path, type(exc).__name__, str(exc)[:200]))
            continue
        (rows.append(row) if row else skipped.append(str(path)))
    rows.sort(key=lambda r: (REVIEW_ORDER.index(r['verdict']), str(r.get('case'))))

    for row in rows:
        why = (row['conflicts'] or row['missing'] or row['supported_by'] or ['-'])[0]
        print('  %-24s %-11s %s' % (row.get('case') or '?', row['verdict'], why))
    print('case verdicts: %d case(s); %s; $0 spent, no requests' % (
        len(rows), ', '.join('%s %d' % (n, sum(1 for r in rows if r['verdict'] == n))
                             for n in REVIEW_ORDER)))
    for path in skipped:
        print('  SKIPPED (no whole-case timeline saved): %s' % path)
    for note in broken:
        print('  UNREADABLE (saved evidence does not load or does not parse): %s' % note)

    if rows or skipped or broken:
        # A run where every case failed still has something to report, and it was writing nothing.
        # Every other writer in this chain routes through case_review.output_path, which refuses a
        # path outside paths.DEALFLOW_DIR and anything under OneDrive - CLAUDE.md's Known Folder
        # Move rule. A raw --out here was the one unguarded write path in the chain.
        # BOTH branches go through the guard. Routing only --out through it left the DEFAULT - the
        # branch that actually runs - writing case numbers and judgment amounts to whatever
        # directory --dossiers pointed at, which is the unguarded write path CLAUDE.md's Known
        # Folder Move rule exists to prevent.
        import case_review
        target = args.out or str(Path(paths[0]).parent / 'case-verdicts.json')
        try:
            out = Path(case_review.output_path(target))
        except ValueError as exc:
            # A refused path is the guard working. Say so in one line instead of a traceback; the
            # verdicts are already on stdout, so nothing is lost but the file.
            print('  no report written, refused by the output guard: %s' % exc)
            return 2
        out.parent.mkdir(parents=True, exist_ok=True)
        # `--out report.md` would make these the same file and the markdown would eat the JSON.
        md = out.with_suffix('.md') if out.suffix.lower() != '.md' else out.with_name(
            out.stem + '-report.md')
        out.write_text(json.dumps(
            {'verdicts': rows, 'no_verdict': {'no_timeline_saved': skipped, 'unreadable': broken},
             'caveat': CAVEAT, 'qualification': QUALIFICATION}, indent=2) + '\n',
            encoding='utf-8')
        md.write_text(render_markdown(rows, skipped, broken), encoding='utf-8')
        print('  report: %s' % out)
        print('  report: %s' % md)
    # A case lost to an unreadable or missing file is not a clean run. Returning 0 as long as one
    # case succeeded let a scheduled caller read a truncated run as fine (eighth review).
    return 0 if rows and not skipped and not broken else 1


if __name__ == '__main__':
    raise SystemExit(main())
