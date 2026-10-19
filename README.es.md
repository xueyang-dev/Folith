# Folith · 译页

<p align="center">
  <img src="folith/resources/brand/folith-lockup.png" alt="Folith·译页 — Agentic Translation Workspace" width="840">
</p>

<p align="center">
  <strong>Un espacio de trabajo de traducción para documentos largos y localización profesional</strong>
</p>

<p align="center">
  <a href="https://github.com/xueyang-dev/Folith/releases/tag/v0.6.0"><img src="https://img.shields.io/badge/release-v0.6.0-blue.svg" alt="Release v0.6.0"></a>
  <img src="https://img.shields.io/badge/macOS-15.7.5%2B%20·%20Apple%20Silicon-lightgrey.svg" alt="macOS 15.7.5+ Apple Silicon">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
</p>

<p align="center">
  <a href="README.md">简体中文</a> | <a href="README.en.md">English</a> | <a href="README.ru.md">Русский</a> | <strong>Español</strong>
</p>

> La versión que se mantiene es el [README en chino simplificado](README.md). Esta página es una traducción; si difieren, prevalece la china. La interfaz de la aplicación está en chino simplificado por defecto, así que los nombres de los controles se dan como aparecen en la interfaz en español (una configuración regional experimental).

---

**Folith · 译页** («páginas traducidas») es un banco de trabajo de escritorio para traducir libros, informes técnicos, monografías académicas y otros documentos largos. Trata un documento como un trabajo completo: primero lee todo el texto y construye un perfil y un glosario; después traduce por lotes con contexto. Un revisor independiente comprueba cada segmento, y los segmentos que editas a mano se vuelven a revisar. Cuando la puerta de entrega se supera, todo se congela en un paquete de entrega trazable.

Traducir un pasaje es fácil. Lo difícil es usar las mismas palabras en el capítulo nueve que en el dos, dejar constancia de cada cambio y poder explicar el origen de un archivo entregado. Folith está hecho para eso.

---

## 📸 Capturas de pantalla

### Biblioteca de tareas
Las tareas se agrupan por proyecto. Cada tarjeta muestra una miniatura de la primera página original, el número de segmentos, los idiomas y la etapa actual. Puedes filtrar por estado, buscar en todo el texto y reanudar una tarea interrumpida con un clic.

<p align="center">
  <img src="docs/assets/qt-01-tasks.png" alt="Biblioteca de tareas de Folith" width="1000">
</p>

### Proyectos: base terminológica y memoria de traducción
La base terminológica y la memoria de traducción se guardan por proyecto. Los términos confirmados se usan automáticamente en las tareas nuevas; los pares de oraciones que superan la revisión entran en la memoria del proyecto y no se mezclan con proyectos ajenos. Se admite importar y exportar XLSX / TBX / TMX.

<p align="center">
  <img src="docs/assets/qt-02-projects.png" alt="Base terminológica de proyecto de Folith" width="1000">
</p>

### Banco de traducción
Original y traducción lado a lado, segmento a segmento; la barra de pasos superior muestra Preparar → Traducción → Revisión → Entrega. El panel derecho ofrece acciones para el segmento actual: revisar, comentar, retraducir según tus indicaciones, hacer una pregunta, añadir a la base terminológica, anotar.

<p align="center">
  <img src="docs/assets/qt-03-workbench.png" alt="Banco de traducción de Folith" width="1000">
</p>

### Puerta de entrega
Mientras queden segmentos sin revisar o problemas bloqueantes, la entrega se detiene y se explica el motivo. Aun así puedes exportar una copia de trabajo de la traducción actual.

<p align="center">
  <img src="docs/assets/qt-04-delivery-gate.png" alt="Puerta de entrega de Folith" width="1000">
</p>

### Entrega congelada
Tras la revisión, el resultado se congela en una versión de entrega inmutable con su manifiesto. Después se pueden generar PDF, EPUB, TMX, TBX y JSONL a partir de la traducción terminada, sin volver a traducir.

<p align="center">
  <img src="docs/assets/qt-05-delivery-ready.png" alt="Entrega congelada de Folith" width="1000">
</p>

> Las capturas las genera `scripts/qt_showcase_screenshots.py` con documentos de demostración sintéticos; no contienen archivos reales de usuarios.

---

## 🚀 Instalación

### Cliente de escritorio para macOS (recomendado)

Descarga `Folith-Qt-0.6.0-macos-arm64.dmg` de la [versión v0.6.0](https://github.com/xueyang-dev/Folith/releases/tag/v0.6.0) y arrástralo a Aplicaciones.

- Requisitos: macOS 15.7.5 o posterior, Apple Silicon.
- Esta compilación no está firmada ni notarizada. La primera vez, haz clic derecho en Folith dentro de Aplicaciones → Abrir y permítelo una sola vez. **No** desactives Gatekeeper de forma global.
- Tesseract (inglés, chino simplificado y árabe) viene incluido, así que no hay que instalar nada más.
- Al abrirla, añade una conexión de modelo en Ajustes (véase «Inicio rápido»).

### Windows

La interfaz Qt compartida está adaptada a Windows, pero **todavía no hay instalador para Windows ni se ha hecho la aceptación en Windows**. Solo se puede ejecutar desde el código fuente, sin probar; no se recomienda para trabajo real.

### Ejecutar desde el código fuente (desarrolladores)

```bash
git clone https://github.com/xueyang-dev/Folith.git
cd Folith
python -m pip install -e .
python -m folith.qt
```

Requiere Python 3.10+; PySide6 forma parte de las dependencias. Opcionalmente, instala [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) para localizar las líneas de las páginas escaneadas. Sin él, las páginas escaneadas pasan por completo al modelo de visión.

### Paquete de Python (para desarrolladores)

El paquete `foliothread` existe en PyPI (el módulo del código se llama `folith`), pero **no es la forma recomendada de obtener la aplicación**. La versión 0.6.0 de PyPI todavía contiene la interfaz Streamlit antigua: el comando `folith` inicia esa interfaz, no el cliente de escritorio. La interfaz antigua se ha eliminado del código fuente; a partir de la próxima versión el comando `folith` iniciará directamente el cliente de escritorio. Hasta entonces, usa el DMG o ejecuta desde el código fuente.

---

## ⚡ Inicio rápido

1. **Añade una conexión de modelo**: abre Ajustes, en Modelo elige un proveedor, introduce la clave de API y el nombre del modelo, pulsa Probar conexión y guarda.
2. **Importa un documento**: en la biblioteca de tareas pulsa Importar documento… y elige un archivo PDF / DOCX / EPUB / TXT / Markdown.
3. **Elige un modo**: en la página de preparación, Rápido, Estándar o Exhaustivo. Cada tarjeta muestra el número estimado de llamadas, los tokens de entrada y el tiempo para este documento.
4. **Traduce y revisa**: pulsa Iniciar traducción. Al terminar, revisa segmento a segmento en el banco y atiende los elementos de Revisar y corregir, a la derecha.
5. **Congela la entrega**: cuando los problemas estén resueltos y la puerta se supere, pulsa Congelar y guardar entrega…, elige dónde guardar y obtendrás la traducción, la copia bilingüe, el glosario, la memoria de traducción y el manifiesto.

---

## ⚡ Características

| Etapa | Qué ocurre |
| :--- | :--- |
| **Preparar** | Extrae segmentos y descarta ruido como encabezados y pies; las páginas escaneadas las lee el modelo de visión y Tesseract local solo da la posición de cada línea; genera el perfil del documento y candidatos a término, e incorpora los términos y la memoria del proyecto |
| **Traducir** | Lotes según el modo (Rápido / Estándar / Exhaustivo), cada uno con contexto circundante y términos bloqueados; el progreso se guarda continuamente y se puede pausar y reanudar |
| **Revisar** | Un modelo revisor distinto comprueba cada segmento frente al original y ve las traducciones vecinas; edición manual, retraducción según indicaciones, preguntas sobre un segmento; los segmentos editados se vuelven a revisar |
| **Entregar** | La puerta verifica la integridad y los problemas bloqueantes; se congela una versión inmutable; se exportan traducción, copia bilingüe, términos, memoria y manifiesto |

- **📚 Importación de documentos largos**: PDF / DOCX / EPUB (sin DRM) / TXT / Markdown.
- **🧠 Contexto de todo el libro**: antes de traducir se hace un perfil del documento y una extracción de términos, de modo que pronombres, abreviaturas y términos concuerden entre páginas.
- **💎 Recursos lingüísticos por proyecto**: los términos bloqueados se inyectan en cada solicitud; la memoria de traducción reutiliza pares de oraciones revisados dentro del proyecto y reduce llamadas repetidas.
- **🔍 Revisión independiente y comprobaciones entre segmentos**: la revisión la hace un modelo aparte, no el traductor evaluándose a sí mismo; los lotes se agrupan por oraciones, así que una oración nunca se parte. Los problemas se clasifican por categoría y gravedad, y solo los bloqueantes detienen la entrega.
- **🛡️ Responsabilidad humana**: las ediciones manuales se registran fielmente y se revisan de nuevo antes de aceptarse; el estado de la tarea se guarda continuamente, y una tarea interrumpida se reanuda desde el último lote completado.
- **📦 Entrega trazable**: DOCX solo con traducción, DOCX bilingüe, EPUB, PDF, glosario, TMX, JSONL y un manifiesto `delivery_manifest.json`; una versión congelada no se puede modificar.
- **🎓 Modo académico** (desactivado por defecto): exporta el proceso de traducción, la comparación de casos y la reflexión como informe de prácticas, para contextos como la evaluación de másteres de traducción (MTI).

Aquí «agéntico» significa concretamente: un rol de revisor independiente, retraducir un segmento según tus indicaciones y preguntar al modelo sobre un segmento. No cambia tu documento por su cuenta; cada cambio lo confirmas tú y queda registrado.

---

## ⚙️ Modelos y proveedores

- **Ajustes preconfigurados**: OpenAI, Gemini, DeepSeek, OpenRouter, SiliconFlow, Moonshot / Kimi, Zhipu / GLM, Qwen / DashScope, OpenCode Go.
- **Pasarelas propias**: cualquier pasarela compatible con la API de OpenAI o servidor de modelos local (vLLM / Ollama).
- **Varias conexiones**: traducción, revisión y visión pueden usar cada una una conexión guardada distinta; la de visión solo se valida si la tarea contiene páginas escaneadas.
- **Catálogo de modelos**: los proveedores compatibles con OpenAI y Gemini pueden obtener la lista de modelos disponibles; que un modelo funcione de verdad lo decide Probar conexión.
- **Esfuerzo de razonamiento**: admite modelos de razonamiento (como DeepSeek-R1 y la serie o de OpenAI); el razonamiento se puede ajustar o desactivar.

---

## 🔒 Privacidad y datos

- **Quién recibe qué**: el texto del documento y las imágenes de las páginas escaneadas se envían a los proveedores de modelos **que tú configures**, y se tratan según sus condiciones de privacidad. Asegúrate de que puedes procesar el documento de ese modo.
- **No se sube nada más**: la aplicación no tiene telemetría, analítica ni cuentas. Aparte de los proveedores de modelos, la única petición de red es leer la información de la última versión en GitHub cuando pulsas Buscar actualizaciones; no se descarga ni instala nada automáticamente.
- **Los datos se quedan en tu equipo**: tareas, términos, memoria de traducción y ajustes están en el directorio de datos local — macOS: `~/Library/Application Support/Folith/data`; Windows: `%LOCALAPPDATA%\Folith\data`.
- **Cómo se guardan las claves de API**: en un archivo de configuración JSON local dentro del directorio de datos (permisos 0600, legible solo por el usuario actual). **No** están en el llavero del sistema y no tienen cifrado adicional. No sincronices el directorio de datos en lugares compartidos ni lo compartas con otras personas.

---

## 💰 Coste

Folith es gratuito y de código abierto. Las llamadas a modelos que se hacen al traducir las factura tu proveedor, con tu propia clave de API. La página de preparación estima llamadas, tokens de entrada y tiempo para cada modo (Rápido / Estándar / Exhaustivo) para que puedas compararlos antes de empezar. La estimación es orientativa; el consumo real depende del documento y del modelo.

---

## ⚠️ Limitaciones conocidas

- El DMG solo admite Apple Silicon y macOS 15.7.5 o posterior; no está firmado ni notarizado.
- Windows solo funciona desde el código fuente: sin instalador y sin aceptación.
- No se admiten EPUB con DRM.
- Las imágenes dentro de tablas y cuadros de texto no se conservan en el DOCX traducido; el DOCX bilingüe no contiene imágenes.
- No se escribe de vuelta en el diseño del DOCX original; se generan archivos nuevos.
- Los idiomas de interfaz distintos del chino simplificado son experimentales.
- Las páginas escaneadas las reconoce el modelo de visión y pueden contener errores; compáralas con la página original.
- Siete elementos de aceptación manual (aprobación con doble clic en Finder, panel de archivos del sistema, servicio de modelo real, método de entrada chino, salir desde el Dock, suspensión / reactivación, Mac limpio) se siguen verificando tras la publicación; véanse las [notas de la versión](docs/qt-beta/release-notes.md) (en chino).

---

## 🌐 Idiomas de la interfaz

La interfaz está en chino simplificado por defecto. English, Español, Русский y العربية son experimentales: los textos están completos, pero no han tenido una revisión lingüística independiente y el RTL del árabe no está plenamente aceptado. Véase [Apariencia y límites multilingües](docs/i18n/README.md).

---

## ❓ Preguntas frecuentes

**¿macOS bloquea el primer arranque?** La aplicación no está firmada ni notarizada. En Aplicaciones, haz clic derecho en Folith, elige Abrir y vuelve a pulsar Abrir en el cuadro de diálogo. Solo hace falta una vez. No desactives Gatekeeper globalmente.

**¿Falla Probar conexión?** Revisa la clave de API, el nombre del modelo y la dirección. Si hay un proxy del sistema o una VPN activa, puede devolver errores como 502; prueba a desactivarla o a excluir el dominio del proveedor. Con una pasarela propia, asegúrate de que sea compatible con la API de OpenAI.

**¿Una página escaneada se reconoció mal?** El reconocimiento lo hace el modelo de visión y puede equivocarse. Compara con la página original en el banco; puedes pasar a un modelo de visión más potente configurando una conexión de visión aparte en Ajustes y volver a escanear.

**¿Se interrumpió una tarea?** El progreso se guarda continuamente. Abre la tarea en la biblioteca y pulsa Continuar traducción; se reanuda desde el último lote completado y los lotes que tuvieron éxito no se repiten.

**¿Cómo actualizo, desinstalo o borro mis datos?** Buscar actualizaciones te avisa si hay una versión más reciente; descárgala tú mismo desde la página de Releases. Arrastrar `Folith.app` a la Papelera elimina solo la aplicación; las tareas y los ajustes se conservan. Para borrar todo, cierra la aplicación y elimina `~/Library/Application Support/Folith`, `~/Library/Logs/Folith` y `~/Library/Preferences/com.folith.QtDesktop.plist`. Exporta antes las traducciones y los TMX / TBX que quieras conservar.

### Comentarios

- Errores y sugerencias: [GitHub Issues](https://github.com/xueyang-dev/Folith/issues)
- Correo: xydev@outlook.sa (también accesible desde Ajustes en la aplicación)

---

## 🗺️ Hoja de ruta

- [x] **v0.4.0**: marca unificada, recursos lingüísticos y centro de terminología, puerta de entrega, recuperación de borradores en vivo
- [x] **v0.6.0**: el cliente de escritorio nativo Qt pasa a ser la entrada principal; importación de EPUB / TXT / Markdown; el modelo de visión como lector principal de páginas escaneadas; términos y memoria de traducción por proyecto; varias conexiones de modelo; revisión entre segmentos; el DOCX traducido conserva las imágenes del original; la apariencia sigue al sistema
- [ ] **Próximamente**: firma y notarización; instalador y aceptación para Windows; escritura de vuelta en DOCX conservando diseño y estilos originales; coincidencias aproximadas en la memoria de traducción; traducción incremental entre versiones de un documento

---

## 📖 Documentación

(La mayoría de los documentos están en chino.)

- [Notas de la versión de escritorio](docs/qt-beta/release-notes.md)
- [Plan de publicación](docs/release-plan.md)
- [Proyectos y memoria a largo plazo](docs/project-memory.md)
- [Exportación de la traducción a EPUB / DOCX](docs/epub-export.md)
- [OCR y lectura con visión](docs/ocr-subsystem.md)
- [Arquitectura y límites de capacidad](docs/architecture-boundaries.md)
- [Registro de cambios](CHANGELOG.md)

---

## 📄 Licencia

Publicado bajo la [licencia MIT](LICENSE).
