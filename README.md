# Cuéntica MCP

MCP server para acceder a la API de [Cuéntica](https://cuentica.com) desde Claude, Cursor, Windsurf u cualquier cliente compatible con el protocolo MCP.

Desarrollado por [UNGRYNERD](https://ungrynerd.com).

---

## Qué puedes hacer

- 📄 Listar, crear, actualizar y eliminar facturas y borradores
- 💰 Consultar facturas pendientes de cobro (paginación automática)
- ✅ Marcar facturas y gastos como cobrados/pagados
- 📧 Enviar facturas por email al cliente
- 🧾 Crear, consultar y gestionar gastos e ingresos
- 👥 Gestión completa de clientes y proveedores (CRUD)
- 🏦 Ver cuentas bancarias y traspasos
- 📁 Gestionar documentos del buzón de Cuéntica
- 🏷️ Gestionar etiquetas
- 📊 Resúmenes trimestrales de IVA

---

## Requisitos

- Python 3.10 o superior
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (recomendado) o `pip`
- Cuenta activa en [Cuéntica](https://cuentica.com) con acceso a la API

---

## Obtener el token de API

1. Inicia sesión en [app.cuentica.com](https://app.cuentica.com)
2. Ve a **Configuración → API → Generar token**
3. Copia el token — lo necesitarás en la configuración

---

## Requisito previo: instalar `uv`

Este paquete se ejecuta con [`uv`](https://docs.astral.sh/uv/), la herramienta estándar para MCPs en Python. Si no lo tienes:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# macOS con Homebrew
brew install uv
```

No hace falta instalar Python ni crear entornos virtuales — `uv` lo gestiona todo automáticamente.

---

## Instalación

### Claude Desktop

Edita tu archivo de configuración:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "cuentica": {
      "command": "uvx",
      "args": ["cuentica-mcp"],
      "env": {
        "CUENTICA_API_TOKEN": "tu_token_aquí"
      }
    }
  }
}
```

`uvx` descarga e instala el paquete automáticamente la primera vez. No necesitas clonar el repositorio ni instalar nada más.

Reinicia Claude Desktop tras editar el archivo.

### Claude Code

```bash
claude mcp add -s user cuentica -e CUENTICA_API_TOKEN=tu_token -- uvx cuentica-mcp
```

El flag `-s user` lo añade globalmente, disponible en todos tus proyectos.

### Cursor / Windsurf / otros clientes MCP

Consulta la documentación de tu cliente para añadir un MCP server con:
- **Comando**: `uvx`
- **Args**: `cuentica-mcp`
- **Variable de entorno**: `CUENTICA_API_TOKEN=tu_token`

---

## Desarrollo local

Si quieres modificar el código:

```bash
git clone https://github.com/UNGRYNERD/cuentica-mcp
cd cuentica-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
CUENTICA_API_TOKEN=tu_token python -m cuentica_mcp.server
```

Para usarlo en Claude Desktop apuntando a tu copia local:

```json
"args": ["--from", "/ruta/a/cuentica-mcp", "cuentica-mcp"]
```

---

## Ejemplos de uso

Una vez configurado, habla con el LLM en lenguaje natural:

```
"¿Qué facturas tengo pendientes de cobro?"
"Muéstrame los gastos del primer trimestre de 2026"
"¿Cuánto he facturado a este cliente en lo que va de año?"
"Marca la factura 30/2026 como cobrada"
"Envíale la factura 28/2026 por email a cliente@empresa.com"
"¿Qué borradores tengo para emitir este mes?"
"Resumen de IVA del primer trimestre"
"¿Cuánto dinero tengo en mis cuentas?"
"Crea una factura de 1.000 € + IVA para el cliente X"
```

---

## Herramientas disponibles

### Empresa
| Herramienta | Descripción |
|---|---|
| `get_company` | Datos del negocio |
| `get_invoice_series` | Series de facturación |

### Facturas
| Herramienta | Descripción |
|---|---|
| `list_invoices` | Listar con filtros |
| `get_invoice` | Detalle de una factura |
| `create_invoice` | Crear factura ⚠️ |
| `update_invoice` | Actualizar factura ⚠️ |
| `delete_invoice` | Eliminar factura ⚠️ |
| `get_invoice_public_link` | Link público |
| `get_invoice_pdf` | Descargar PDF |
| `update_invoice_charges` | Marcar como cobrada ⚠️ |
| `send_invoice_email` | Enviar por email ⚠️ |
| `void_invoice` | Anular (Verifactu) ⚠️ |

### Gastos
| Herramienta | Descripción |
|---|---|
| `list_expenses` | Listar con filtros |
| `get_expense` | Detalle de un gasto |
| `create_expense` | Crear gasto ⚠️ |
| `update_expense` | Actualizar gasto ⚠️ |
| `delete_expense` | Eliminar gasto ⚠️ |
| `update_expense_payments` | Marcar como pagado ⚠️ |
| `get_expense_attachment` | Obtener adjunto |
| `update_expense_attachment` | Actualizar adjunto ⚠️ |
| `delete_expense_attachment` | Eliminar adjunto ⚠️ |

### Ingresos
| Herramienta | Descripción |
|---|---|
| `list_income` | Listar con filtros |
| `get_income` | Detalle de un ingreso |
| `create_income` | Crear ingreso ⚠️ |
| `update_income` | Actualizar ingreso ⚠️ |
| `delete_income` | Eliminar ingreso ⚠️ |
| `update_income_charges` | Actualizar cobros ⚠️ |
| `get_income_attachment` | Obtener adjunto |
| `update_income_attachment` | Actualizar adjunto ⚠️ |
| `delete_income_attachment` | Eliminar adjunto ⚠️ |

### Clientes
| Herramienta | Descripción |
|---|---|
| `list_customers` | Buscar clientes |
| `get_customer` | Detalle de un cliente |
| `create_customer` | Crear cliente ⚠️ |
| `update_customer` | Actualizar cliente ⚠️ |
| `delete_customer` | Eliminar cliente ⚠️ |

### Proveedores
| Herramienta | Descripción |
|---|---|
| `list_providers` | Buscar proveedores |
| `get_provider` | Detalle de un proveedor |
| `create_provider` | Crear proveedor ⚠️ |
| `update_provider` | Actualizar proveedor ⚠️ |
| `delete_provider` | Eliminar proveedor ⚠️ |

### Cuentas bancarias
| Herramienta | Descripción |
|---|---|
| `list_accounts` | Listar cuentas |
| `get_account` | Detalle de una cuenta |

### Documentos
| Herramienta | Descripción |
|---|---|
| `list_documents` | Listar buzón |
| `get_document` | Detalle de un documento |
| `create_document` | Subir documento ⚠️ |
| `update_document` | Asignar a gasto / cambiar fecha ⚠️ |
| `delete_document` | Eliminar documento ⚠️ |
| `get_document_attachment` | Obtener adjunto en Base64 |

### Etiquetas y traspasos
| Herramienta | Descripción |
|---|---|
| `list_tags` | Etiquetas disponibles |
| `list_transfers` | Listar traspasos |
| `get_transfer` | Detalle de un traspaso |
| `create_transfer` | Crear traspaso ⚠️ |
| `update_transfer` | Actualizar traspaso ⚠️ |
| `delete_transfer` | Eliminar traspaso ⚠️ |

> ⚠️ Las herramientas marcadas crean, modifican o eliminan datos. El LLM pedirá confirmación explícita antes de ejecutarlas.

---

## Comportamiento inteligente

El servidor está configurado para:

- **Excluir facturas anuladas** automáticamente de totales y pendientes (`status_description == "voided"`)
- **Filtrar borradores futuros**: los borradores con fecha en años futuros son facturas recurrentes preprogramadas, no pendientes reales
- **Paginar correctamente**: en consultas de "pendientes", itera todas las páginas automáticamente
- **Limitar el tamaño de página** a un máximo de 50 resultados para no saturar el contexto
- **Proteger el token**: nunca lo mostrará completo en una respuesta

---

## Limitaciones

- La API de Cuéntica tiene un límite de **600 peticiones cada 5 minutos**
- La API es Swagger 2.0 — los tipos de datos siguen ese esquema

---

## Licencia

MIT — úsalo, modifícalo y compártelo libremente.

---

## Contribuir

Pull requests bienvenidos. Si encuentras un endpoint útil que falta o un bug, abre un issue.
