# Draft #332 — Task 1b copy proposal

Alex’s October 22 decision: preserve the approved dated English first touch; translate that exact email into Spanish. Early/no-date, tax-deed, follow-up and final proposals remain unchanged in both languages.

## Verification

- Restored main’s bodyColdEN selection, original arguments and preceding comment block. The board dated English body is byte-for-byte the rendered outreach_copy.email_body_template(). Its English fallback is restored too.
- The dated Spanish body has the same 11 non-empty paragraphs, same claims, call durations, booking/reply/call ask, and corresponding sender/date/company/phone/identity values. It ends in sigES and the exact approved opt-out line.
- All other 16 rendered bodies and subjects are unchanged from the previous draft head.
- Six tests pass. The new exact-English assertion fails on previous PR head d06a455 before this fix. Existing pair/closing mutation tests remain for the other variants and closings are checked across all 18.
- No protected sender, suppression, bankruptcy, disclaimer, shared-copy, .bat, or paid API surface changed. Draft only; no send or deployment.

## Content scores

Local deliverability.score_content only: not mailbox placement proof. Spanish phrase coverage is limited. The approved English first email is preserved regardless of its heuristic score.

| Variant | Stage | Language | Score |
|---|---|---|---:|
| early | cold | en | 0 |
| early | cold | es | 0 |
| early | follow | en | 2.0 |
| early | follow | es | 2.0 |
| early | final | en | 2.0 |
| early | final | es | 2.0 |
| tax | cold | en | 0 |
| tax | cold | es | 0 |
| tax | follow | en | 2.0 |
| tax | follow | es | 2.0 |
| tax | final | en | 2.0 |
| tax | final | es | 2.0 |
| dated | cold | en | 1.5 |
| dated | cold | es | 1.0 |
| dated | follow | en | 2.0 |
| dated | follow | es | 2.0 |
| dated | final | en | 2.0 |
| dated | final | es | 2.0 |

## Rendered dated English reference

```text
Hi Synthetic,

You're probably getting a lot of mail and calls about your property, so I'll keep this short.

Your home is scheduled for auction on 12/31/2099.
If nothing changes before that date, the house is sold to the highest bidder and the choices you have right now go with it. It doesn't have to go that way.

My name is Synthetic Advisor with Example Company. I am not your lender, not the government, not a foreclosure-rescue company, and not an attorney.
Our senior advisor has spent over 30 years in mortgages and foreclosure workouts, and he personally reviews each case. He has sat across from people in your position and walked them through it with their equity intact.

If you're open to it, I'd like to set up a short phone call. Most take about five minutes; I block fifteen so you are never rushed. It costs you nothing, and you decide what happens next.

On that call we'll:

  • Go over your case and where things stand.
  • Walk through the options you have, including some you may not have heard of.
  • Give you a clear, honest read on the way forward.

You can pick a time at cal.com/bsgflorida/free-records-review, reply with the best number and time to reach you, or call me directly at 305-555-0100.

Best regards,

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

If now's not a good time, just tell me and I won't reach out again.
```

## Full rendered Spanish dated first touch

```text
Hola Synthetic,

Probablemente esté recibiendo mucha correspondencia y muchas llamadas sobre su propiedad, así que seré breve.

Su casa está programada para subastarse el 12/31/2099.
Si nada cambia antes de esa fecha, la casa se vende al mejor postor y las opciones que tiene ahora desaparecen con ella. No tiene por qué ser así.

Mi nombre es Synthetic Advisor, de Example Company. No soy su prestamista, no soy del gobierno, no soy una empresa de rescate de ejecuciones, y no soy abogado.
Nuestro asesor principal lleva más de 30 años trabajando en hipotecas y soluciones para casos de ejecución hipotecaria, y revisa personalmente cada caso. Se ha sentado frente a personas en su situación y las ha acompañado en el proceso, manteniendo su capital en la propiedad intacto.

Si está dispuesto, me gustaría coordinar una breve llamada telefónica. La mayoría dura unos cinco minutos; reservo quince para que nunca se sienta apurado. No le cuesta nada, y usted decide qué pasa después.

En esa llamada:

  • Revisaremos su caso y en qué situación se encuentra.
  • Repasaremos las opciones que tiene, incluidas algunas que quizás no conozca.
  • Le daremos una evaluación clara y honesta de cómo seguir adelante.

Puede elegir un horario en cal.com/bsgflorida/free-records-review, responder con el mejor número y horario para contactarlo, o llamarme directamente al 305-555-0100.

Saludos cordiales,

Synthetic Advisor
Example Company
Phone: 305-555-0100
1 Example Street

Si ahora no es buen momento, solo dígamelo y no lo vuelvo a contactar.
```

## Other retained variants

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
