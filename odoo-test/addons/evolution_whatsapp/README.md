# Evolution WhatsApp · Odoo 18

Integración nativa de **WhatsApp en Odoo 18** vía la API de [Evolution Go](https://docs.evolutionfoundation.com.br/evolution-go/), con plantillas QWeb e integración en el chatter de cualquier modelo (CRM, Ventas, Facturas, Compras, etc).

## Características

* **Instancia única por empresa** — conectar por QR, monitorear estado, eliminar.
* **Composer integrado** — botón "Enviar por WhatsApp" en el menú Acción de:
  Contactos, Leads (CRM), Pedidos (Ventas), Facturas (Contabilidad), Pedidos de compra,
  Tareas (Project), Tickets (Helpdesk), Albaranes (Stock).
* **Plantillas QWeb** — reutilizables con placeholders `{{ object.partner_id.name }}`,
  análogas a `mail.template` y `sms.template`.
* **Tipos de mensaje** — texto, imagen, video, audio, documento (PDF, etc).
* **Adjuntos automáticos** — generación de reporte (PDF de la factura, presupuesto)
  adjunto al mensaje.
* **Chatter** — cada envío queda registrado en el chatter del documento origen.
* **Envío masivo** — seleccionás varios registros y enviás a todos.
* **Webhook** — controller listo para recibir eventos entrantes y actualizar estados.

## Instalación

1. Copiá `evolution_whatsapp/` dentro de tu `addons_path` de Odoo 18.
2. Activá modo desarrollador → Apps → Actualizar lista de apps.
3. Buscá **"Evolution WhatsApp"** → Instalar.

El `post_init_hook` crea automáticamente bindings para `sale`, `crm`, `account`,
`purchase`, `project`, `helpdesk` y `stock` si están instalados.

## Configuración

Hay **una sola instancia por empresa**. WhatsApp → Configuración → **Instancia**
(la abre o la crea si todavía no existe) → pestaña "Conexión":

| Campo                       | Ejemplo                       |
|-----------------------------|-------------------------------|
| URL Evolution Go            | `https://evo.midominio.com`   |
| API Key global              | (la del servidor)             |
| URL pública de Odoo         | `https://odoo.midominio.com`  |
| Código de país por defecto  | `54`                          |

## Flujo completo

### 1. Conectar la instancia

WhatsApp → Configuración → **Instancia**.

1. Nombre (`ventas_bot`).
2. **Crear en Evolution** → genera token.
3. **Obtener QR** → escaneá desde el celular.
4. Estado pasa a **Conectado**.

### 2. Crear una plantilla

WhatsApp → **Plantillas** → Nueva.

- **Modelo aplicable**: ej `sale.order`.
- **Instancia**: la que se va a usar.
- **Tipo**: Texto, Imagen, Documento, etc.
- **Campo de teléfono**: opcional, ej `partner_id.mobile` (si no, auto-detecta).
- **Cuerpo**:
  ```
  Hola {{ object.partner_id.name }} 👋
  Tu pedido {{ object.name }} fue confirmado.
  Total: {{ object.amount_total }} {{ object.currency_id.name }}
  ```
- Opcional: **Reporte a adjuntar** (ej: PDF del presupuesto).

### 3. Enviar desde cualquier modelo

Abrí un pedido / factura / lead / contacto → menú ⚙️ **Acción → Enviar por WhatsApp**:

- Se abre el composer ya apuntando al registro.
- Elegí la plantilla (el body se renderiza automáticamente).
- (Opcional) Cambiá el teléfono manual, el tipo de mensaje o subí otro archivo.
- **Enviar** → el envío se loguea en el chatter del registro.

### 4. Envío masivo

En la vista lista, seleccioná varios registros → **Acción → Enviar por WhatsApp**:

- Composer pasa a modo masivo, muestra cuántos registros.
- Si hay plantilla, se renderiza por cada registro.
- Cada envío se loguea individualmente y se postea al chatter de cada uno.

### 5. Manual sin registro

WhatsApp → **Enviar mensaje** (menú principal):

- Composer en modo standalone.
- Pegás los números separados por coma.
- Mandás como antes.

### 6. Recibir mensajes (webhook)

En el form de la instancia, pestaña "Webhook", copiá el `Webhook URL`:
```
https://<odoo>/evolution/webhook/<token>
```

Configurá ese URL como webhook de la instancia en Evolution Go.
El controller maneja: `qrcode.updated`, `connection.update`, `messages.upsert/update`.

## Modelos

| Modelo                  | Descripción                                                |
|-------------------------|------------------------------------------------------------|
| `evolution.instance`    | Una instancia (número) de WhatsApp                         |
| `evolution.message`     | Historial de mensajes salientes e entrantes               |
| `whatsapp.template`     | Plantilla reutilizable con QWeb inline                     |
| `whatsapp.composer`     | Composer de envío (single / mass / standalone)             |

## Cómo integrar otro modelo

Tres formas:

**A. Manual** (un clic):
Ajustes → Técnico → Acciones de servidor → Crear:
- Modelo: el que quieras
- Modelo objeto vinculado: el mismo
- Tipo de acción: Ejecutar Python
- Código: `action = env['whatsapp.composer'].action_open_for_records(records)`

**B. Por código** en otro módulo: depender de `evolution_whatsapp` y crear el binding en data.

**C. Agregar al hook**: editá `hooks.py:AUTO_BIND_MODELS` y agregá `"modulo": "tu.modelo"`.

## Server actions

| Acción                    | Path Evolution Go      |
|---------------------------|-------------------------|
| Crear instancia           | `POST /instance/create` |
| Listar instancias         | `GET /instance/all`     |
| QR code                   | `GET /instance/qr`      |
| Estado                    | `GET /instance/status`  |
| Logout                    | `POST /instance/logout` |
| Eliminar                  | `DELETE /instance/delete` |
| Enviar texto              | `POST /send/text`       |
| Enviar media              | `POST /send/media`      |
| Ubicación                 | `POST /send/location`   |
| Contacto                  | `POST /send/contact`    |
| Presencia                 | `POST /chat/presence`   |

Auth: header `apikey` (global para crear/listar; token de instancia para el resto).

## Licencia

LGPL-3
