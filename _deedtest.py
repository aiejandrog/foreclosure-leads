"""Quit claim deed forms: approval gate, grantor parsing, merge fields, execution requirements."""
import asyncio, os, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from playwright.async_api import async_playwright
SRC = pathlib.Path(os.path.expanduser('~/OneDrive/Desktop/DEALFLOW/Foreclosure Lead Tracker.html'))
ok=[];bad=[]
def rec(n,c,d=''):
    (ok if c else bad).append(n); print(('  PASS ' if c else '  FAIL ')+n+((' | '+str(d)) if d else ''))

JS = r"""() => {
  const out = {};
  // grantor parsing — the half-interest-deed bug
  out.parse = {
    two:    _grantors({owners:'SMITH JOHN; SMITH MARY'}),
    andW:   _grantors({owners:'TAWNYA JENKINS BROWN &H CHARLES'}),
    amp:    _grantors({owners:'LUIS RIVERA & BETSY RIVERA'}),
    one:    _grantors({owners:'PETE SHOMADE'}),
    none:   _grantors({owners:''}),
    dupes:  _grantors({owners:'JOHN DOE; JOHN DOE'})
  };
  // blocked while unapproved
  const r = DATA.find(x => _grantors(x).length > 1) || DATA[0];
  const solo = DATA.find(x => _grantors(x).length === 1) || DATA[0];
  const blocked = genQuitClaim(r, {multi:true});
  out.blockedWhenUnapproved = blocked.indexOf('awaiting attorney approval') > -1
                           || blocked.indexOf('has not been approved') > -1;
  out.fingerprintShown = /[0-9a-f]{8}/.test(blocked);
  out.approvedFlag = _docApproved('quitclaim', _qcSrc());

  // now approve it in-memory and render for real
  ATTY_APPROVALS.quitclaim = {by:'TEST', date:'2026-07-30', fingerprint:_docFingerprint(_qcSrc())};
  // A single-grantor deed on a TWO-owner property conveys a half interest. Asking for the single
  // form on a multi-owner lead must auto-upgrade rather than quietly do the dangerous thing.
  const upgraded = genQuitClaim(r, {multi:false});
  const single = genQuitClaim(solo, {multi:false});   // genuinely one owner
  const multi  = genQuitClaim(r, {multi:true});
  const owners = _grantors(r);
  const f = _docFields(r);

  const sigBlocks = h => (h.match(/Signature \(Grantor/g) || []).length;
  // Labels changed 2026-07-31 to "Witness 1 &mdash; Signature" / "Witness 2 &mdash; Signature"
  // (numbered + em-dash) so each witness's name/address rows can be paired unambiguously.
  const witnesses = h => (h.match(/Witness \d+ &mdash; Signature/g) || []).length;

  out.rendered = {
    singleSigs: sigBlocks(single), multiSigs: sigBlocks(multi),
    upgradedSigs: sigBlocks(upgraded), soloOwners: _grantors(solo).length,
    singleWit: witnesses(single),  multiWit: witnesses(multi),
    ownersFound: owners.length,
    // merge fields actually present
    hasFolio:  r.folio ? multi.indexOf(r.folio) > -1 : null,
    hasLegal:  r.legal ? multi.indexOf(String(r.legal).slice(0,24)) > -1 : null,
    hasAddr:   r.addr  ? multi.indexOf(String(r.addr).split(',')[0]) > -1 : null,
    hasOwner:  owners.length ? multi.indexOf(owners[0]) > -1 : null,
    // county must be dynamic, not the template's hardcoded MIAMI-DADE
    countyDynamic: multi.indexOf((f.county||'').toUpperCase()) > -1,
    // the $10 must NOT be hardcoded
    tenHardcoded: /consideration of the sum of \$\s*10\.00/.test(multi),
    // execution requirements present
    has689:  multi.indexOf('689.01') > -1,
    has695:  multi.indexOf('695.03') > -1,
    hasHomestead: multi.indexOf('4(c)') > -1 || multi.indexOf('§4(c)') > -1,
    hasNotaryConflict: multi.indexOf('117.107') > -1,
    has501:  multi.indexOf('501.1377') > -1,
    hasDocStamp: multi.indexOf('201.02') > -1,
    hasUPL: multi.indexOf('Furman') > -1,
    // plural acknowledgment on the multi form only
    singlePlural: single.indexOf('is/are') > -1,
    upgradedPlural: upgraded.indexOf('is/are') > -1,
    multiPlural:  multi.indexOf('is/are') > -1,
    // the checklist must be marked do-not-record
    doNotRecord: multi.indexOf('DO NOT RECORD') > -1,
    // no NaN leaks
    nan: multi.indexOf('NaN') > -1 || multi.indexOf('undefined') > -1
  };
  ATTY_APPROVALS.quitclaim = {by:'', date:'', fingerprint:''};   // restore
  return out;
}"""

async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(); pg=await b.new_page()
        errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(SRC.as_uri()); await pg.wait_for_timeout(3000)
        d = await pg.evaluate(JS)
        P = d['parse']; R = d['rendered']

        rec('splits semicolon co-owners', P['two']==['SMITH JOHN','SMITH MARY'], P['two'])
        rec('splits county-roll "&H" / "&W" shorthand', len(P['andW'])==2, P['andW'])
        rec('splits ampersand couples', len(P['amp'])==2, P['amp'])
        rec('single owner stays single', P['one']==['PETE SHOMADE'], P['one'])
        rec('no owner returns empty, not a phantom grantor', P['none']==[], P['none'])
        rec('duplicate names collapse', P['dupes']==['JOHN DOE'], P['dupes'])

        rec('deed will NOT print without attorney approval', d['blockedWhenUnapproved'])
        rec('block page shows the fingerprint to unlock with', d['fingerprintShown'])
        rec('ships unapproved by default', not d['approvedFlag'])

        rec('single form has exactly 1 grantor signature block',
            R['singleSigs']==1, f"{R['singleSigs']} block, {R['soloOwners']} owner")
        rec('asking for SINGLE on a multi-owner lead auto-upgrades (no half-interest deed)',
            R['upgradedSigs']>=2 and R['upgradedPlural'], f"{R['upgradedSigs']} blocks")
        rec('multi form has one block per owner', R['multiSigs']>=2, f"{R['multiSigs']} blocks / {R['ownersFound']} owners")
        rec('two witness lines per grantor (FS 689.01)', R['multiWit']==R['multiSigs']*2,
            f"{R['multiWit']} witness lines / {R['multiSigs']} grantors")
        rec('acknowledgment is plural only on the multi form',
            R['multiPlural'] and not R['singlePlural'])

        rec('folio merges in', R['hasFolio'] is not False, R['hasFolio'])
        rec('legal description merges in', R['hasLegal'] is not False, R['hasLegal'])
        rec('property address merges in', R['hasAddr'] is not False, R['hasAddr'])
        rec('grantor name merges in', R['hasOwner'] is not False, R['hasOwner'])
        rec('county is dynamic, not hardcoded MIAMI-DADE', R['countyDynamic'])
        rec('the $10 consideration is a FIELD, not hardcoded', not R['tenHardcoded'])

        rec('cites two-witness rule (FS 689.01)', R['has689'])
        rec('cites recording/acknowledgment (FS 695.03)', R['has695'])
        rec('cites homestead spousal joinder (Art. X §4(c))', R['hasHomestead'])
        rec('cites interested-notary bar (FS 117.107)', R['hasNotaryConflict'])
        rec('cites foreclosure-rescue statute (FS 501.1377)', R['has501'])
        rec('cites doc stamps on assumed mortgage (FS 201.02)', R['hasDocStamp'])
        rec('names the UPL case (Fla. Bar v. Furman)', R['hasUPL'])
        rec('checklist page marked DO NOT RECORD', R['doNotRecord'])
        rec('no NaN/undefined in the instrument', not R['nan'])
        rec('no JS errors', not errs, errs[:2])
        await b.close()
    print(f'\n==== {len(ok)}/{len(ok)+len(bad)} deed checks passed ====')
    return 1 if bad else 0
raise SystemExit(asyncio.run(main()))
