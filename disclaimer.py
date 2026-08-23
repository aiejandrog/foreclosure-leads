"""disclaimer — the ONE source of the compliance language that goes to homeowners.

WHY THIS EXISTS
The same disclaimer was hand-copied into four modules and had drifted into five different versions.
Audited 2026-08-22, before this file existed:

    surface            MARS/Reg O   not a law firm   not a lender/broker   not a rescue company
    bsg_letter            yes            yes                yes                   NO
    bsg_flyer             yes            yes                NO                    NO
    outreach_mail         partial        yes                yes                   yes
    outreach_email        NO             yes ("lawyer")     yes                   yes (added 08-22)

Every cell that says NO was a sentence someone had already written on another surface and simply
forgotten here. That is what a copy-pasted disclaimer does — it does not stay copied. The version a
regulator or a plaintiff's lawyer reads back to you is always the weakest one you shipped, so the
weakest one is the only one that matters.

WHAT IS IN HERE

  MARS_*      The 12 CFR 1015.4(a) (MARS / Reg O) disclosures. Federal wording — you may ADD to it,
              never subtract from it or contradict it. Goes on anything printed and handed or mailed
              to a homeowner: letters, flyers, door hangers.
  IDENTITY_*  The "who I am not" sentence that opens a letter or an email. Not federally scripted;
              this is the FS 501.1377 / UPL denial in plain language.
  SIG_TAG_*   The two-line tag under a signature block where the full MARS text will not fit.

HOW TO USE IT
    import disclaimer as D
    D.mars('Biscayne Solutions Group')          # letter — company name filled in
    D.mars('%s')                             # flyer — leaves a %s for %-formatting
    D.mars(lang='es', as_html=False)         # plain unicode instead of HTML entities
    D.identity()                             # "I am not your lender, ..."

Spanish is stored ONCE in real unicode and the HTML-entity form is derived (`as_html=True`, the
default). Do not add a second hand-entitied copy — that is the drift this file exists to stop.

NOT LEGAL ADVICE. This file is a consistency mechanism, not a legal opinion. The wording came from
copy already in use across the letters and Lob mail; a Florida attorney should sign off on the
consolidated text before the next print or send run. See the OPEN QUESTION at the bottom.
"""

# ---------------------------------------------------------------------------------------------
# Accent -> HTML entity. One plain-unicode source, one derived rendering, no second copy.
# ---------------------------------------------------------------------------------------------
_ENT = {
    'á': '&aacute;', 'é': '&eacute;', 'í': '&iacute;', 'ó': '&oacute;', 'ú': '&uacute;',
    'Á': '&Aacute;', 'É': '&Eacute;', 'Í': '&Iacute;', 'Ó': '&Oacute;', 'Ú': '&Uacute;',
    'ñ': '&ntilde;', 'Ñ': '&Ntilde;', 'ü': '&uuml;', 'Ü': '&Uuml;',
    '¿': '&iquest;', '¡': '&iexcl;',
}


def _ent(s):
    return ''.join(_ENT.get(c, c) for c in s)


# ---------------------------------------------------------------------------------------------
# MARS / Reg O — 12 CFR 1015.4(a). {co} is the company name.
#
# Sentence order follows bsg_letter's 2026-08-12 legal pass: the mandated disclosures come FIRST,
# at body-copy size, then the house denials. bsg_flyer used to lead with "not a law firm", which
# pushed the federally-required sentences behind a house line; it now matches the letter.
#
# "We are not a foreclosure-rescue company" is the clause added 2026-08-22 at Alejandro's
# direction. It was already on the Lob mail and on nothing else. See the OPEN QUESTION below.
# ---------------------------------------------------------------------------------------------
_MARS_EN = (
    "{co} is not associated with the government, and our service is not approved by the "
    "government or your lender. Even if you use our service, your lender may not agree to "
    "change your loan. You may stop doing business with us at any time. We are not a law "
    "firm and do not give legal advice. We are not a lender or mortgage broker and we do not "
    "quote loan rates or terms. We are not a foreclosure-rescue company. Consultations are "
    "always free."
)

_MARS_ES = (
    "{co} no está asociado con el gobierno, y nuestro servicio no está aprobado por el "
    "gobierno ni por su banco. Aunque use nuestro servicio, es posible que su banco no acepte "
    "modificar su préstamo. Puede dejar de trabajar con nosotros en cualquier momento. No "
    "somos un bufete de abogados y no damos consejos legales. No somos prestamista ni "
    "corredor hipotecario y no cotizamos tasas ni términos. No somos una empresa de rescate "
    "de ejecuciones. Las consultas siempre son gratis."
)

# ---------------------------------------------------------------------------------------------
# IDENTITY — the opening "who I am not" sentence. First person: it is said by Jose or Carlos,
# not by the entity. Keep all four denials; each one closes a different door:
#   lender        -> not a mortgage originator (Reg O / licensing)
#   government    -> not HUD, not the clerk, not the county (the impersonation complaint)
#   rescue        -> not a FS 501.1377 foreclosure-rescue consultant
#   attorney      -> not UPL
# ---------------------------------------------------------------------------------------------
_IDENTITY_EN = ("I am not your lender, not the government, not a foreclosure-rescue company, "
                "and not an attorney.")

_IDENTITY_ES = ("No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de "
                "ejecuciones, y no soy abogado.")

# ---------------------------------------------------------------------------------------------
# SIG_TAG — signature-block tag for surfaces with no room for the full MARS block. It is a
# SUPPLEMENT, never a substitute: anything printed and handed to a homeowner carries mars().
# ---------------------------------------------------------------------------------------------
_SIG_TAG_EN = "Not a law firm. Not a HUD-approved housing counselor. Not a foreclosure-rescue company."
_SIG_TAG_ES = ("No somos un bufete de abogados. No somos un consejero de vivienda aprobado por HUD. "
               "No somos una empresa de rescate de ejecuciones.")

_MARS = {'en': _MARS_EN, 'es': _MARS_ES}
_IDENTITY = {'en': _IDENTITY_EN, 'es': _IDENTITY_ES}
_SIG_TAG = {'en': _SIG_TAG_EN, 'es': _SIG_TAG_ES}


def _pick(table, lang):
    try:
        return table[(lang or 'en').lower()[:2]]
    except KeyError:
        raise ValueError("lang must be 'en' or 'es', got %r" % (lang,))


def mars(company='', lang='en', as_html=True):
    """MARS/Reg O 12 CFR 1015.4(a) disclosures.

    company  — the name to drop in. Pass '%s' to leave a positional placeholder for a caller that
               %-formats its template later (bsg_flyer does this). Pass '' for a generic "We".
    as_html  — True (default) renders Spanish accents as HTML entities, for templates that keep
               their source ASCII. False returns real unicode, for plain-text bodies.
    """
    text = _pick(_MARS, lang)
    if not company:
        # No name supplied: the sentence still has to work. English reads "We are not associated";
        # Spanish "No estamos asociados". This is the pre-2026-08-22 flyer phrasing, kept as a
        # fallback only — a named company is stronger and every current caller passes one.
        text = (text.replace('{co} is not associated', 'We are not associated')
                    .replace('{co} no está asociado', 'No estamos asociados'))
    else:
        text = text.replace('{co}', company)
    return _ent(text) if as_html else text


def identity(lang='en', as_html=True):
    """The opening 'I am not your lender / the government / a rescue company / an attorney'."""
    text = _pick(_IDENTITY, lang)
    return _ent(text) if as_html else text


def sig_tag(lang='en', as_html=True):
    """Short signature-block tag. Supplements mars(), never replaces it."""
    text = _pick(_SIG_TAG, lang)
    return _ent(text) if as_html else text


# ---------------------------------------------------------------------------------------------
# OPEN QUESTION FOR THE ATTORNEY (raised 2026-08-22, not resolved here)
#
# mars() now carries the federally-scripted MARS disclosures AND the sentence "We are not a
# foreclosure-rescue company", on the same piece of paper.
#
# There is a real tension in that pairing. The MARS Rule (12 CFR 1015) governs *mortgage assistance
# relief service* providers — the §1015.4(a) disclosures are, in substance, what such a provider is
# required to say. Volunteering them while simultaneously denying that you are one can be read two
# ways: as belt-and-braces caution, or as an admission that the disclosures apply to you followed by
# a denial that contradicts it.
#
# Both readings are survivable and neither is obviously right. It is a lawyer's call, not a
# developer's, and it should be made before the next print run rather than in a deposition. The
# question to put to counsel: on a MARS-disclosed letter, does the rescue-company denial help, or
# does it create an inconsistency worth more than it buys? If counsel says drop it, delete the one
# sentence from _MARS_EN/_MARS_ES and it disappears from every surface at once — which is the
# reason this file exists.
# ---------------------------------------------------------------------------------------------

if __name__ == '__main__':
    for lang in ('en', 'es'):
        print('=' * 78)
        print('%s  —  mars("Biscayne Solutions Group")' % lang.upper())
        print('-' * 78)
        print(mars('Biscayne Solutions Group', lang, as_html=False))
        print()
        print('%s  —  identity()' % lang.upper())
        print('-' * 78)
        print(identity(lang, as_html=False))
        print()
        print('%s  —  sig_tag()' % lang.upper())
        print('-' * 78)
        print(sig_tag(lang, as_html=False))
        print()
