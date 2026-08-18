#!/usr/bin/env python
"""objections_es — Spanish for the Core 10 objection cards.

WHY THIS FILE EXISTS
The Jesse canon has essentially no Spanish: one door opener and three closing phrases. Zero of the
40 objections exist in Spanish, while roughly half of Miami-Dade doors are Spanish-dominant. Call
Mode was therefore shipping an honest but useless "No Spanish version of this card yet" on every
objection. This is that gap filled.

⚠️ NEW COPY, NOT CANON. Everything here is a faithful translation of the English drill pack, not
something Jesse said. It carries the same compliance weight as anything spoken to a homeowner and
should be read by Alejandro (and ideally counsel) before it is used in anger. The English remains
the source of truth; if the two ever disagree, the English wins and this file is the bug.

RULES OBSERVED IN EVERY LINE
  * USTED throughout. Never tú. The playbook grades Spanish drills on register, and every Spanish
    asset in this repo (flyer, letter, voicemail, emails) is usted.
  * Miami street register: "la corte", "el caso", "la subasta" — not "el tribunal", "el expediente
    judicial", "el remate". Say it the way it is said on a Hialeah porch, not the way it is written
    in a Spain-localised legal dictionary.
  * LANGUAGE LAW: "nuestro asesor principal" (never "mi supervisor"); the 30-plus years belong to
    HIM, never to the company; we INTRODUCE licensed lenders, we never "place loans"; nothing about
    the attorney's case merits, money side only; never a promised outcome.
  * CIOC shape preserved exactly: paragraph 1 = cushion + isolate, ending on an open/closed
    question. Paragraph 2 = overcome (ONE reframe, ONE analogy, ONE what-if) + a fairness close.
  * The closes are the canon's own: "¿Verdad que sí?" / "¿No le parece?" / "Usted no pierde nada."
"""

# key = objection number in the Core 10 (see MSG Objection Drill Pack - Procrastinator Psychology.md)
ES = {
    1: {  # The Bank Mod Shield
        'say': 'El banco ya está trabajando conmigo en una modificación. No quiero dañar eso hablando con otra persona.',
        'reb': [
            'Muy bien, y siga con esa modificación — nada de lo que hacemos la toca. Déjeme preguntarle una sola cosa: '
            'si hablar conmigo no pudiera afectar la modificación en lo absoluto, ¿habría alguna otra razón para no '
            'escuchar diez minutos, o es solamente eso?',
            'Esto es lo que casi nadie sabe: en la Florida la revisión de la modificación y el caso de la corte corren '
            'por carriles separados. La fecha de la corte sigue avanzando mientras el banco "revisa", y las cartas de '
            'negación llegan a menudo pocos días antes de la subasta. Es como reparar el motor mientras el carro sigue '
            'rodando hacia el semáforo: la reparación es real, pero el semáforo no espera. ¿Y si la modificación le '
            'llega negada con dos semanas por delante — quiere estar viendo sus opciones ese día, o tenerlas ya listas? '
            'Sin costo y sin compromiso. ¿Verdad que sí?',
        ],
        'one': 'La revisión de la modificación y la fecha de la subasta corren por carriles separados — la corte no se '
               'detiene mientras el banco "revisa".',
    },
    2: {  # The Lawyer Shield
        'say': 'Mi abogado se está encargando. Hable con él, no conmigo.',
        'reb': [
            'Quédese con su abogado — en serio, nosotros trabajamos CON los abogados, nunca por encima de ellos, y '
            'tener defensa puesta lo pone por delante de la mayoría de la gente con la que hablo. Una pregunta nada '
            'más: su abogado está defendiendo el caso. ¿Alguien se ha sentado con usted a mostrarle qué pasa con su '
            'equidad cuando la defensa se acabe?',
            'La defensa compra tiempo; no decide qué hace usted con ese tiempo. Mientras tanto los honorarios del banco '
            'y los intereses diarios corren contra su equidad cada mes que el caso se alarga. Su abogado juega a la '
            'defensa, pero nadie está jugando a la ofensiva con su dinero. ¿Y si el juez falla en contra en la próxima '
            'audiencia y le quedan semanas y no meses — hay un plan para ese día, o solamente la apelación? Diez '
            'minutos gratis con nuestro asesor principal, más de treinta años en esto, coordinado con su abogado, sin '
            'honorarios nunca. Si su abogado dice que no hacemos falta, nos damos la mano y quedamos como amigos.',
        ],
        'one': 'Su abogado juega a la defensa — nadie está jugando a la ofensiva con su equidad mientras el reloj corre.',
    },
    3: {  # The Postponement Gambler
        'say': 'Esa fecha de subasta no significa nada. Ya la cancelaron dos veces. Siempre la empujan.',
        'reb': [
            'Tiene razón en que las subastas se posponen — usted lo ha vivido dos veces, así que no le voy a decir que '
            'las fechas nunca se mueven. Pero déjeme preguntarle: ¿sabe POR QUÉ se pospusieron esas dos? Porque cada '
            'cancelación tuvo un motivo — una revisión, una moción — y esos motivos se agotan.',
            'Y esta es la parte que nadie menciona: cada posposición se la cobraron a usted. El interés después del '
            'fallo corre a diario — ponga setenta dólares o más por día sobre un fallo de trescientos mil — más '
            'honorarios nuevos de abogado en cada reprogramación, todo saliendo de su equidad. Cuando una por fin se '
            'sostiene, el certificado de título puede salir unos diez días después de la subasta, y ahí todas las '
            'opciones se van a cero. Es el huracán que se desvió dos veces: el tercero no sabe de su historial. ¿Y si '
            'esta es la fecha que se queda y le quedan diez días? Tener el plan listo no le cuesta nada — gratis, sin '
            'honorarios, sin compromiso. Si se pospone otra vez, no perdimos nada y quedamos como amigos.',
        ],
        'one': 'Cada posposición se la cobraron a su equidad a setenta dólares por día — y el huracán que se desvió dos '
               'veces no sabe de su historial.',
    },
    4: {  # The Incoming Check
        'say': 'Estoy esperando mi reembolso de impuestos y un cheque de un acuerdo. Cuando entre, pongo todo al día.',
        'reb': [
            'Dinero en camino es un activo real — no se lo estoy quitando. Déjeme aislar una sola cosa: si ese cheque '
            'entra antes de la fecha de la subasta, usted está bien. Entonces la única pregunta es qué pasa si no '
            'entra. ¿Le parece justo?',
            'El problema es este: su cheque corre en el reloj del IRS o en el reloj de un ajustador de seguros — sin '
            'fecha límite, se atrasa meses, y hasta se lo pueden descontar. La ejecución corre en el reloj de la corte, '
            'con un juez y una fecha. Solamente uno de esos dos relojes tiene horario. ¿Y si el cheque entra tres '
            'semanas después de la subasta? Diez minutos gratis y tiene un plan B que no le cuesta nada tener guardado. '
            '¿No le parece?',
        ],
        'one': 'Su cheque corre en el reloj del IRS; la ejecución corre en el reloj del juez — solamente uno de los dos '
               'tiene horario.',
    },
    5: {  # The Family Money
        'say': 'Mi hermano me está prestando el dinero. Lo estamos resolviendo en familia.',
        'reb': [
            'Esa es la mejor ayuda que hay — dinero de familia y sin condiciones. Una pregunta: ¿lo único que falta '
            'entre usted y arreglar esto es que llegue el dinero, o alguien ya pidió la cifra exacta de reinstalación '
            'por escrito al banco?',
            'Aquí está la trampa: esa cifra no son sus pagos atrasados. Son los pagos, más los cargos por mora, más los '
            'honorarios del abogado del banco, más el interés diario — sobre un fallo de trescientos mil, unos setenta '
            'u ochenta dólares por día. Es como pedir el precio del pasaje una semana antes de volar: el número que '
            'usted tiene en la cabeza no es el número de hoy. Y la carta oficial con esa cifra el banco se demora una '
            'semana o más en darla. ¿Y si el dinero de su hermano alcanza para el número viejo pero no para el nuevo? '
            'Diez minutos gratis con nuestro asesor principal y usted sabe la cifra real antes de pedirle nada a su '
            'familia. ¿Verdad que sí?',
        ],
        'one': 'La cifra de reinstalación no son sus pagos atrasados — son pagos más cargos más setenta dólares diarios, '
               'y el banco se demora una semana en darle esa carta.',
    },
    6: {  # The Bankruptcy Pause Button
        'say': 'Mi abogado va a radicar bancarrota. Eso detiene todo esto, así que no hay nada de qué hablar.',
        'reb': [
            'Bien pensado tener esa opción lista — la parada automática es real y sí detiene la subasta. Nada más para '
            'entenderlo bien: si la bancarrota solamente pausa esto en vez de terminarlo, ¿valdría diez minutos saber '
            'qué pasa el día que se levante la pausa?',
            'La bancarrota es un botón de pausa, no un borrador. El prestamista normalmente radica una moción para '
            'levantar la parada, y esas se ven en treinta a sesenta días — y ahí el reloj arranca exactamente donde se '
            'quedó, con los mismos atrasos y ahora con honorarios nuevos encima. Es pausar la película: cuando le da '
            'play, la escena sigue igualita. ¿Y si le levantan la parada en cuarenta días y usted no tiene nada '
            'preparado para ese día? La consulta es gratis y no interfiere con su abogado. ¿No le parece justo?',
        ],
        'one': 'La bancarrota es un botón de pausa, no un borrador — cuando se levanta la parada usted está en la misma '
               'escena y con los mismos atrasos.',
    },
    7: {  # The Scam Fatigue Wall
        'say': 'Ya hablé con tres de ustedes. Todos quieren lo mismo. Váyase de mi puerta.',
        'reb': [
            '¿Honestamente? Usted debería desconfiar — la mayoría de los que tocan quieren comprarle la casa barata, '
            'hoy, con un contrato en la mano. Se lo pregunto directo: si yo no vengo a comprarle la casa y no le voy a '
            'pedir ni una firma ni un centavo hoy, ¿hay alguna otra razón para no escuchar dos minutos?',
            'Nosotros empezamos con una consulta gratis con nuestro asesor principal — más de treinta años haciendo '
            'esto — y le ponemos sobre la mesa de tres a cinco opciones, incluyendo las que le dejan la casa. Nunca le '
            'cobramos un centavo por adelantado, y eso no es un favor, es la ley. Tres mecánicos malos no quieren decir '
            'que el motor se arregla solo. ¿Y si en cinco minutos le señalamos algo que ninguno de esos tres vio? '
            'Usted no pierde nada — ¿verdad?',
        ],
        'one': 'Tres mecánicos malos no quieren decir que el motor se arregla solo — y yo no vengo con un contrato.',
    },
    8: {  # The Wrong House (Denial)
        'say': 'Usted tiene la casa equivocada. Aquí no hay ninguna ejecución.',
        'reb': [
            'Ojalá tenga la casa equivocada — de verdad, sería la mejor noticia de mi día. ¿Le puedo preguntar una sola '
            'cosa, nada más para dejarlo tranquilo? Si se hubiera radicado un caso en la corte con esta dirección, '
            '¿usted querría saberlo, o preferiría que no le dijera nada?',
            'Porque un caso radicado no es un veredicto sobre usted — es un reloj, y los que lo miran temprano son los '
            'que se quedan con opciones. Es como un resultado de laboratorio guardado en una gaveta: leerlo no lo '
            'enferma, y no leerlo no lo cura. ¿Y si le doy el número del caso y usted mismo lo verifica en el récord de '
            'la corte, sin hablar conmigo nunca más? No le cuesta nada y queda tranquilo de una forma u otra. ¿No le '
            'parece?',
        ],
        'one': 'Si se hubiera radicado algo bajo esta dirección, ¿preferiría ser el primero en enterarse o el último?',
    },
    9: {  # It's Too Late (Hopelessness)
        'say': 'Ya hay fecha de subasta. Se acabó, ya no hay nada que nadie pueda hacer.',
        'reb': [
            'Lo escucho — y después de meses con esto, estar cansado tiene todo el sentido del mundo. Pero déjeme '
            'preguntarle una sola cosa: ¿es que no se puede hacer nada, o es que usted ya no puede más? Porque esas son '
            'dos cosas distintas.',
            'Una fecha de subasta es una fecha límite, no un veredicto — las subastas se posponen, los casos se '
            'reestructuran, y hasta en el camino más duro hay una diferencia enorme entre salir con dinero en la mano y '
            'salir sin nada. Es el partido en el último minuto: ahí es cuando las jugadas importan más, no menos. ¿Y si '
            'en cinco minutos gratis nuestro asesor principal le muestra una jugada que todavía queda? Si no queda '
            'ninguna, se lo decimos de frente y quedamos como amigos.',
        ],
        'one': 'Una fecha de subasta es una fecha límite, no un veredicto — y los últimos diez minutos son cuando las '
               'jugadas más importan.',
    },
    10: {  # She Doesn't Know (Family Secret)
        'say': 'Mi esposa no sabe lo mal que está esto. Tiene que irse antes de que ella salga.',
        'reb': [
            'Me hago para atrás — y quiero que sepa que entiendo por qué ha cargado esto solo; usted la estaba '
            'protegiendo. Una pregunta tranquila antes de irme: ¿preferiría que ella lo escuche de usted, con un plan '
            'en la mano, o de un papel pegado en la puerta?',
            'Porque un secreto así tiene fecha de vencimiento, y la pone la corte, no usted. Decírselo con opciones es '
            'como el piloto que anuncia la turbulencia junto con la ruta para esquivarla: da miedo un segundo y después '
            'todo el mundo respira. Decírselo al final es el aterrizaje de emergencia. ¿Y si diez minutos gratis y '
            'privados — usted y el asesor, antes de cualquier conversación en la casa — le dieran las palabras y el '
            'plan para llevárselo a ella? ¿No es justo que cuando ella se entere, se entere de que usted ya estaba '
            'peleando por ella?',
        ],
        'one': 'Ella se va a enterar — lo único que queda por decidir es si lo escucha de usted con un plan, o de un '
               'aviso en la puerta.',
    },
}

# ---- ROUND TWO: the 8/17 live-call masterclass (cards 11-14) --------------------------------
# Same rules as above: usted, Miami register, language law, CIOC shape. NEW COPY, not canon.
ES.update({
    11: {
        'say': 'Sinceramente estoy agotado. No puedo con esto ni aunque bajara un milagro del cielo. '
               'Mejor lo dejo todo.',
        'reb': [
            'Lo entiendo, y la mayoria de las personas con las que hablamos llegan a ese mismo punto '
            '— quieren alejarse de todos los problemas, y es justo. Dejeme preguntarle una sola cosa: '
            'que es lo que usted VERDADERAMENTE quiere hacer con esta propiedad?',
            'Si de verdad ya no puede mas, en vez de que el banco se quede con la casa Y con su '
            'plusvalia, mejor salga con dinero en el bolsillo, en su propia fecha, con dignidad — y '
            'no con el sheriff poniendo sus cosas en la calle un dia que usted no controla. Tenemos '
            'un programa exactamente para esto — Cash for Keys. Acordamos una cifra; le damos la '
            'MITAD por adelantado al firmar, y usted escoge su plazo: 30, 60 o 90 dias. Al final '
            'entrega las llaves, la casa queda libre de sus cosas personales, y recibe la otra mitad '
            'ese mismo dia. Usted escoge la fecha. Usted manda.',
            'Deme quince o veinte minutos para revisar el expediente — lo que se debe, lo que vale — '
            'y regreso con una cifra real. Si nos hace sentido a los dos, hoy mismo movemos el dinero.',
        ],
        'one': 'Salga con dinero y una fecha que USTED escogio — o deje que el sheriff la escoja.',
    },
    12: {
        'say': 'El agente lo tiene listado en $429 mil, a diez dias de la subasta, sin visitas — y '
               'no deja pasar ninguna llamada al dueno.',
        'reb': [
            'No vengo a senalar culpables — si el precio fue idea suya o de su cliente, sinceramente '
            'no me importa. Vengo a detener una ejecucion, a que esto funcione para su cliente, para '
            'usted, y quizas para uno de mis inversionistas. Pero lo primero es aceptar la realidad '
            'de ese precio con una subasta a diez dias.',
            'Y esto es lo bueno para usted: si trabajamos juntos y mi inversionista compra, usted se '
            'queda con la comision COMPLETA de esta venta — yo le cedo mi parte. Y cuando la '
            'revendamos despues del trabajo, usted vuelve a tener el listing. La misma propiedad le '
            'paga dos veces, y nadie mas en este expediente le esta ofreciendo eso. Ahora — su '
            'cliente necesita oir la verdad del numero. Si quiere respaldo, lo hago con usted en la '
            'linea; si prefiere, lo llamo yo aparte; o lo maneja usted solo. Cualquiera de las tres '
            'funciona, pero una de las tres pasa esta semana.',
        ],
        'one': 'La misma propiedad, dos comisiones — si el precio acepta la realidad esta semana.',
    },
    13: {
        'say': 'Ya lo tengo resuelto — el banco esta revisando mi modificacion / el dinero me llega '
               'manana / mi primo me lo manda.',
        'reb': [
            'Lo felicito — hizo lo correcto, y le aplaudo que haya puesto la bola en movimiento. '
            'Siga haciendo exactamente lo que esta haciendo. Una pregunta nada mas: tiene la '
            'aprobacion por escrito? Porque muchos bancos alargan la revision y niegan dos o tres '
            'dias antes de la subasta — y la negacion puede irse directo a sus abogados sin que '
            'usted la vea. Por mucho que no queramos mirarlo, usted estaria de acuerdo en que esa '
            'posibilidad existe, verdad?',
            'Entonces esto es lo que le propongo — yo no quiero ser su banco, ni su intermediario. '
            'Quiero ser su paracaidas. Usted esta apostando su casa a que ese plan llegue a tiempo; '
            'dejenos trabajar el aplazamiento de la venta en paralelo, para que si el dinero o la '
            'aprobacion llega un dia tarde, la subasta ya este detenida y su plan todavia salve la '
            'casa. Si su plan funciona, perfecto — le hice un favor y me debe uno. Y la version sin '
            'costo: deme una autorizacion de tercero — solo permiso para hablar con su banco, nada '
            'mas, sin honorarios — hago una llamada de cortesia y le traigo la verdad de donde esta '
            'su expediente. Con eso decidimos si trabajamos juntos o no. Justo?',
        ],
        'one': 'Usted esta apostando su casa al calendario — quedese con su plan, y dejeme ser el paracaidas.',
    },
    14: {
        'say': 'Nada — este es SU movimiento al final de toda llamada que fue bien, antes de colgar.',
        'reb': [
            'Hagame un favor antes de colgar. Yo he invertido mi tiempo y voy a seguir '
            'invirtiendolo despues de esta llamada — asi que guarde mi numero ahora mismo. Mi nombre '
            'es Jesse. La compania es Miami Solutions Group — pongale MSG, asi le sale en el '
            'identificador. Este es mi celular personal, y le doy tambien el de la oficina. Ahora '
            'leamelo todo de vuelta — quiero asegurarme de que lo tiene bien.',
            'Si lo lee de vuelta completo, esta DENTRO. Si "se le acabo la tinta" o le pide que se '
            'lo repita, esta tibio — apriete el seguimiento y no cuente el negocio. El mismo musculo '
            'en tamano grande: el cierre de papeleria. "Dejeme preparar los papeles — se los mando '
            'por correo, los repasamos, y si estamos de acuerdo nos damos la mano. Si no, perdi '
            'quince minutos de mi tiempo y quedamos como amigos. Usted no arriesga nada. Le parece '
            'justo?" Y cuando estan tibios pero necesitan autoridad: "Pongase al telefono con '
            'nuestro asesor principal cinco minutos y le encontramos una solucion a esta propiedad."',
        ],
        'one': 'Si lo lee de vuelta completo esta dentro — si "se acabo la tinta", no lo esta.',
    },
})
