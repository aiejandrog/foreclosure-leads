# Draft #332: bilingual homeowner email proposal

Task 1 only. Rebased on main 28864c0. This changes board composer copy; it does not change automated sender files, disclaimer.py, outreach_copy.py, gates or any paid call.

## Routing correction

The old dated English path selected the separately baked English-only body instead of bodyEN. The board now selects the matching EN/ES pair for dated cold email. Early, tax, follow and final use corresponding bilingual copy. Each variant explicitly asks for a call or text to the sender number, with replying with a time as an alternative.

All bodies end in their original sigEN/sigES block. The exact opt-out closings and the identity disclosures are retained. Subjects and recipient handling are unchanged. This remains a draft for Alex; nothing is sent or deployed.

## Regression evidence

On current main, test_every_rendered_pair_has_matching_meaning_ask_and_exact_closing fails: early/cold/en has no proposal message concepts (concepts set() differs from the required paired concepts). The old #332 draft also fails because the explicit call/text ask is missing and dated English bypasses the proposal.

After the change, four tests pass across all 18 rendered variants. Negative tests reject a missing Spanish variant, missing/altered closing in each of 18 variants, and Spanish-only meaning drift. The audit renders the actual baked globals and genEmail branch selection under Node with fabricated inputs; it does not replace the rendered opt-out sentence with a test copy.

## Content scores

Existing deliverability.score_content is a local heuristic, not an inbox placement test. Its mainly English phrase rules make Spanish scores weak evidence. Follow/final retain Re: subject threading, accounting for 2 points. Before refers to the prior #332 draft, not the historic 18.8 template.

| Variant | Stage | Language | Prior draft | Updated |
|---|---|---|---:|---:|
| early | cold | en | 0 | 0 |
| early | cold | es | 0 | 0 |
| early | follow | en | 2.0 | 2.0 |
| early | follow | es | 2.0 | 2.0 |
| early | final | en | 2.0 | 2.0 |
| early | final | es | 2.0 | 2.0 |
| tax | cold | en | 0 | 0 |
| tax | cold | es | 0 | 0 |
| tax | follow | en | 2.0 | 2.0 |
| tax | follow | es | 2.0 | 2.0 |
| tax | final | en | 2.0 | 2.0 |
| tax | final | es | 2.0 | 2.0 |
| dated | cold | en | 1.5 | 0 |
| dated | cold | es | 0 | 0 |
| dated | follow | en | 2.0 | 2.0 |
| dated | follow | es | 2.0 | 2.0 |
| dated | final | en | 2.0 | 2.0 |
| dated | final | es | 2.0 | 2.0 |

## Rendered proposal

### early / cold / en

Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

I'm writing about 1 Example Street (Case No. SYNTHETIC). Our record does not list an auction date, and it may be out of date.

If you would like to discuss the property with our senior advisor, let me know. If the matter is already handled, there is no need to reply.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### early / cold / es

Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Le escribo sobre 1 Example Street (Caso Número SYNTHETIC). Nuestro registro no indica una fecha de subasta y puede estar desactualizado.

Si desea conversar sobre la propiedad con nuestro asesor principal, dígamelo. Si el asunto ya está resuelto, no necesita responder.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

### early / follow / en

Re: Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

I'm following up on my earlier note about 1 Example Street. If you would like to discuss the property, let me know. If the matter is already handled, there is no need to reply.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### early / follow / es

Re: Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Le escribo para dar seguimiento a mi mensaje sobre 1 Example Street. Si desea conversar sobre la propiedad, dígamelo. Si el asunto ya está resuelto, no necesita responder.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

### early / final / en

Re: Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

This is my last note about 1 Example Street. If you would like to discuss the property, you can contact me. I won't send another follow-up after this note.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### early / final / es

Re: Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Esta es mi última nota sobre 1 Example Street. Si desea conversar sobre la propiedad, puede comunicarse conmigo. No enviaré otro mensaje de seguimiento después de esta nota.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

### tax / cold / en

Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

I'm writing about 1 Example Street (Certificate/Case No. SYNTHETIC). Our record lists a tax-deed sale date of 12/31/2099 and may be out of date; please check the current status with the county.

If you would like to discuss the property with our senior advisor, let me know.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### tax / cold / es

Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Le escribo sobre 1 Example Street (Número de certificado/caso SYNTHETIC). Nuestro registro indica una subasta de tax deed para el 12/31/2099 y puede estar desactualizado; confirme el estado actual con el condado.

Si desea conversar sobre la propiedad con nuestro asesor principal, dígamelo.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

### tax / follow / en

Re: Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

I'm following up on my earlier note about 1 Example Street. If you would like to discuss the property, let me know. If the matter is already handled, there is no need to reply.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### tax / follow / es

Re: Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Le escribo para dar seguimiento a mi mensaje sobre 1 Example Street. Si desea conversar sobre la propiedad, dígamelo. Si el asunto ya está resuelto, no necesita responder.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

### tax / final / en

Re: Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

This is my last note about 1 Example Street. If you would like to discuss the property, you can contact me. I won't send another follow-up after this note.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### tax / final / es

Re: Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Esta es mi última nota sobre 1 Example Street. Si desea conversar sobre la propiedad, puede comunicarse conmigo. No enviaré otro mensaje de seguimiento después de esta nota.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

### dated / cold / en

Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

I'm writing about 1 Example Street (Case No. SYNTHETIC). Our record lists an auction date of 12/31/2099 and may be out of date; please check the current status with the county.

If you would like to discuss the property with our senior advisor, let me know. If the matter is already handled, there is no need to reply.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### dated / cold / es

Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Le escribo sobre 1 Example Street (Caso Número SYNTHETIC). Nuestro registro indica una subasta para el 12/31/2099 y puede estar desactualizado; confirme el estado actual con el condado.

Si desea conversar sobre la propiedad con nuestro asesor principal, dígamelo. Si el asunto ya está resuelto, no necesita responder.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

### dated / follow / en

Re: Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

I'm following up on my earlier note about 1 Example Street. If you would like to discuss the property, let me know. If the matter is already handled, there is no need to reply.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### dated / follow / es

Re: Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Le escribo para dar seguimiento a mi mensaje sobre 1 Example Street. Si desea conversar sobre la propiedad, dígamelo. Si el asunto ya está resuelto, no necesita responder.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

### dated / final / en

Re: Regarding your property at 1 Example Street

```text
Hi Synthetic,

I'm Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.

This is my last note about 1 Example Street. If you would like to discuss the property, you can contact me. I won't send another follow-up after this note.

Call or text me at 305-555-0100, or reply with a time that works for you.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

### dated / final / es

Re: Referente a su propiedad en 1 Example Street

```text
Hola Synthetic,

Soy Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.

Esta es mi última nota sobre 1 Example Street. Si desea conversar sobre la propiedad, puede comunicarse conmigo. No enviaré otro mensaje de seguimiento después de esta nota.

Llámeme o mándeme un texto al 305-555-0100, o responda con un horario que le convenga.

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```
