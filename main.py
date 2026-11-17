import os
import io
import re
import json
import unicodedata
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
import pdfplumber
import requests
from pydantic import BaseModel
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

# ==========================================
# 1. CONFIGURACIÓN DE LA BASE DE DATOS
# ==========================================
motor = create_engine('sqlite:///skillmatch.db', echo=False)
Base = declarative_base()

class Empresa(Base):
    __tablename__ = 'empresas'
    id = Column(Integer, primary_key=True)
    nombre = Column(String)
    plan_actual = Column(String, default="ContratoHeadhunting")
    vacantes = relationship("Vacante", back_populates="empresa")

class Estudiante(Base):
    __tablename__ = 'estudiantes'
    id = Column(Integer, primary_key=True)
    nombres = Column(String)
    correo = Column(String)
    carrera = Column(String)
    texto_cv = Column(Text)
    matches = relationship("Match", back_populates="estudiante")

class Vacante(Base):
    __tablename__ = 'vacantes'
    id = Column(Integer, primary_key=True)
    id_empresa = Column(Integer, ForeignKey('empresas.id'))
    titulo = Column(String)
    descripcion = Column(Text)
    empresa = relationship("Empresa", back_populates="vacantes")
    matches = relationship("Match", back_populates="vacante")

class Match(Base):
    __tablename__ = 'matches'
    id = Column(Integer, primary_key=True)
    id_vacante = Column(Integer, ForeignKey('vacantes.id'))
    id_estudiante = Column(Integer, ForeignKey('estudiantes.id'))
    porcentaje = Column(Float)
    desbloqueado = Column(Boolean, default=False)

    vacante = relationship("Vacante", back_populates="matches")
    estudiante = relationship("Estudiante", back_populates="matches")

Base.metadata.create_all(motor)
Session = sessionmaker(bind=motor)

# Memoria en tiempo real para el desglose de la Matriz A.1, A.2 y A.3
EVALUACIONES_AGENCIA = {}

# ==========================================
# 2. CATÁLOGO DE VACANTES Y CASOS PRÁCTICOS (A.2)
# ==========================================
VACANTES_INICIALES = [
    {
        "empresa": "Grupo Andino Corporativo",
        "titulo": "Analista de Gestión de Proyectos y Administración",
        "area_caso": "Administración",
        "caso_practico": "Caso Práctico (Administración): El área operativa presenta un retraso del 22% en entregas mensuales y sobrecostos administrativos. Elabora un breve informe de gestión indicando qué indicadores (KPIs) revisarías primero y 3 acciones concretas para optimizar el proceso en 30 días.",
        "query_en": "project manager operations analyst",
        "slug_pe": "analista-de-proyectos-operaciones",
        "descripcion": "Gestión de proyectos, planificación estratégica, administración, ingeniería industrial o empresarial. Metodologías ágiles Scrum, Kanban, liderazgo de equipos, comunicación efectiva, optimización de procesos, Excel, informes de gestión y organización."
    },
    {
        "empresa": "DataMetrics Perú",
        "titulo": "Analista de Datos y Business Intelligence",
        "area_caso": "Análisis de datos",
        "caso_practico": "Caso Práctico (Análisis de Datos): Tienes una base de ventas con 50,000 registros con fechas vacías, clientes duplicados y caída de ingresos en la región Sur. Explica paso a paso cómo limpiarías los datos en Excel/SQL y qué visualizaciones clave incluirías en tu reporte de Power BI para la gerencia.",
        "query_en": "data analyst business intelligence",
        "slug_pe": "analista-de-datos-power-bi",
        "descripcion": "Análisis de datos, creación de dashboards y reportes en Power BI, Tableau y Excel avanzado. Consultas en SQL, Python para ciencia de datos, estadística, KPIs, limpieza de datos, toma de decisiones y análisis comercial o financiero."
    },
    {
        "empresa": "Finanzas & Capital SAA",
        "titulo": "Analista de Finanzas y Control Contable",
        "area_caso": "Finanzas y Contabilidad",
        "caso_practico": "Caso Práctico (Finanzas / Contabilidad): Una empresa mediana solicita una línea de crédito comercial por $80,000 USD. Su balance muestra incremento en ventas pero flujo de caja operativo negativo en el último trimestre. ¿Qué ratios financieros evaluarías para determinar su riesgo crediticio y qué asiento o control aplicarías?",
        "query_en": "financial analyst accountant",
        "slug_pe": "analista-financiero-contable",
        "descripcion": "Contabilidad, finanzas corporativas, análisis de estados financieros, flujo de caja, evaluación de riesgo crediticio, conciliaciones bancarias, registros contables, tributación, Excel financiero, SAP o ERP y elaboración de presupuestos."
    },
    {
        "empresa": "TechCorp Innovación",
        "titulo": "Desarrollador Backend Python / API",
        "area_caso": "Ingeniería de Software",
        "caso_practico": "Caso Práctico (Ingeniería de Software): Un endpoint de facturación tarda 9 segundos en responder cuando hay 200 usuarios simultáneos. Describe cómo diagnosticarías el cuello de botella en la base de datos SQL y qué arquitectura o cambios en la API implementarías para bajar el tiempo a menos de 300 ms.",
        "query_en": "python backend developer",
        "slug_pe": "desarrollador-python-backend",
        "descripcion": "Desarrollo de aplicaciones backend con Python, FastAPI, Django o Flask. Manejo de bases de datos SQL, PostgreSQL, MySQL, creación de APIs REST, Git, algoritmos, programación orientada a objetos y resolución de problemas técnicos."
    },
    {
        "empresa": "GrowthLab LATAM",
        "titulo": "Especialista Comercial y Marketing Estratégico",
        "area_caso": "Comercial",
        "caso_practico": "Caso Práctico (Comercial): Necesitamos captar 15 nuevos clientes corporativos B2B en los próximos 60 días con un presupuesto reducido. Desarrolla una estrategia concreta de prospección, embudo de conversión y propuesta de valor para cerrar reuniones efectivas.",
        "query_en": "commercial sales marketing specialist",
        "slug_pe": "especialista-comercial-marketing",
        "descripcion": "Estrategia comercial, captación de clientes B2B, ventas consultivas, gestión de campañas digitales, CRM, negociación, SEO, SEM, Google Ads, comunicación efectiva, análisis de mercado y fidelización de clientes."
    },
    {
        "empresa": "CloudNet Sistemas",
        "titulo": "Ingeniero de Soporte TI, Redes y Ciberseguridad",
        "area_caso": "Soporte TI y Sistemas",
        "caso_practico": "Caso Práctico (TI / Sistemas): El servidor principal de archivos de la oficina deja de responder a las 9:00 AM y 40 usuarios pierden acceso. Detalla el protocolo exacto de diagnóstico de red/sistemas que ejecutarías en los primeros 15 minutos y cómo asegurarías la continuidad operativa.",
        "query_en": "it support systems engineer",
        "slug_pe": "ingeniero-soporte-ti-sistemas",
        "descripcion": "Soporte técnico, administración de sistemas Windows y Linux, redes, hardware, software, seguridad informática, servidores en la nube AWS o Azure, mantenimiento de equipos, atención de incidencias y telecomunicaciones."
    }
]

PREGUNTAS_ENTREVISTA_A2 = [
    "1. ¿Qué funciones desempeñaba en su anterior trabajo o proyecto principal y qué responsabilidades tenía directamente a su cargo?",
    "2. ¿Qué problemas importantes tuvo que resolver, qué herramientas o sistemas utilizaba y qué resultados concretos consiguió?",
    "3. ¿Por qué dejó su anterior empleo (o qué busca en esta nueva etapa profesional)?"
]

def asegurar_catalogo_vacantes(session):
    titulos_existentes = {v.titulo for v in session.query(Vacante).all()}
    for item in VACANTES_INICIALES:
        if item["titulo"] not in titulos_existentes:
            empresa_obj = session.query(Empresa).filter_by(nombre=item["empresa"]).first()
            if not empresa_obj:
                empresa_obj = Empresa(nombre=item["empresa"])
                session.add(empresa_obj)
                session.commit()
            nueva_vac = Vacante(
                id_empresa=empresa_obj.id,
                titulo=item["titulo"],
                descripcion=item["descripcion"]
            )
            session.add(nueva_vac)
    session.commit()

# ==========================================
# 3. EXTRACTOR AVANZADO DE PDF Y CORREO
# ==========================================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

def extraer_datos_pdf(contenido_bytes):
    texto_cv = ""
    correos_en_links = []
    with pdfplumber.open(io.BytesIO(contenido_bytes)) as pdf:
        for pagina in pdf.pages:
            t1 = pagina.extract_text(x_tolerance=2, y_tolerance=3) or ""
            t2 = pagina.extract_text(layout=True) or ""
            texto_cv += t1 + "\n" + t2 + "\n"
            for link in (pagina.hyperlinks or []):
                uri = str(link.get("uri", ""))
                if "@" in uri:
                    correos_en_links.append(uri.replace("mailto:", "").split("?")[0].strip())
            for annot in (pagina.annots or []):
                uri = str(annot.get("uri", ""))
                if "@" in uri:
                    correos_en_links.append(uri.replace("mailto:", "").split("?")[0].strip())
    return texto_cv.strip(), correos_en_links

def detectar_correo_infalible(texto_cv, correos_en_links):
    if correos_en_links:
        return correos_en_links[0].lower()
    texto_limpio = re.sub(r'\s*@\s*', '@', texto_cv)
    texto_limpio = re.sub(r'\s*\.\s*(com|pe|edu|org|net|es|io|co)\b', r'.\1', texto_limpio, flags=re.IGNORECASE)
    patron = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    match = re.search(patron, texto_limpio)
    if match:
        return match.group(0).lower().strip('.')
    for linea in texto_cv.splitlines():
        if "@" in linea:
            linea_compacta = re.sub(r'\s+', '', linea)
            m = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.(?:com|pe|edu|org|net|es|io|co)(?:\.pe)?', linea_compacta, flags=re.IGNORECASE)
            if m:
                return m.group(0).lower()
    return "correo_no_detectado@mail.com"

# ==========================================
# 4. MOTOR SEMÁNTICO + MATRIZ DE 100 PUNTOS (ÍTEM A.1)
# ==========================================
STOPWORDS = {
    "para", "como", "con", "por", "una", "uno", "unos", "unas", "del", "las", "los",
    "que", "mas", "pero", "sus", "este", "esta", "estos", "estas", "entre", "sobre",
    "todo", "toda", "todos", "todas", "otro", "otra", "otros", "otras", "desde",
    "hasta", "donde", "cuando", "quien", "cual", "cuales", "sido", "estar", "tener",
    "hacer", "puede", "pueden", "trabajo", "empresa", "experiencia", "anos", "peru",
    "lima", "the", "and", "for", "with", "from", "that", "this", "are", "you", "our"
}

SINONIMOS_BILINGUES = {
    "proyecto": ["project", "management", "scrum", "agile", "planning"],
    "gestion": ["management", "manager", "operations", "planning", "strategy"],
    "operaciones": ["operations", "operational", "process", "logistics", "workflow"],
    "administracion": ["administration", "business", "management", "operations"],
    "datos": ["data", "analytics", "analysis", "database", "reporting", "bi"],
    "analista": ["analyst", "analysis", "analytics", "research", "specialist"],
    "desarrollador": ["developer", "engineer", "software", "programming", "coding"],
    "sistemas": ["systems", "system", "it", "technology", "infrastructure", "support"],
    "finanzas": ["finance", "financial", "accounting", "budget", "kpi", "contable"],
    "comercial": ["commercial", "sales", "marketing", "business", "customer"]
}

def normalizar_texto(texto):
    texto_sin_acentos = ''.join(
        c for c in unicodedata.normalize('NFD', texto.lower())
        if unicodedata.category(c) != 'Mn'
    )
    palabras = re.findall(r'[a-z0-9+#]{3,}', texto_sin_acentos)
    return [p for p in palabras if p not in STOPWORDS]

def expandir_tokens_bilingue(tokens):
    expandidos = set(tokens)
    for t in list(tokens):
        for clave_es, traducciones_en in SINONIMOS_BILINGUES.items():
            if t.startswith(clave_es[:5]):
                expandidos.update(traducciones_en)
    return expandidos

def calcular_similitud_inteligente(perfil_estudiante, vacante_empresa, usar_llm=False):
    tokens_cv = expandir_tokens_bilingue(normalizar_texto(perfil_estudiante))
    tokens_vac = set(normalizar_texto(vacante_empresa))
    if not tokens_vac or not tokens_cv:
        return 45.0

    puntos = 0.0
    for tv in tokens_vac:
        if tv in tokens_cv:
            puntos += 1.0
        else:
            raiz = tv[:5] if len(tv) >= 5 else tv
            if len(raiz) >= 4 and any(tc.startswith(raiz) or raiz in tc for tc in tokens_cv):
                puntos += 0.68

    proporcion = puntos / max(6, min(35, len(tokens_vac)) * 0.40)
    score = 44.0 + (proporcion * 52.0)
    return round(min(96.0, max(35.0, score)), 1)

def evaluar_matriz_a1(texto_cv, vacante_info):
    """
    Evalúa el CV exactamente con la Matriz de 100 puntos del documento (Ítem A.1):
    - Formación profesional: 15 pts
    - Experiencia en puestos similares: 25 pts
    - Conocimientos técnicos: 25 pts
    - Manejo de herramientas informáticas: 15 pts
    - Logros y responsabilidades anteriores: 10 pts
    - Estabilidad y trayectoria laboral: 10 pts
    """
    texto_low = ''.join(c for c in unicodedata.normalize('NFD', texto_cv.lower()) if unicodedata.category(c) != 'Mn')
    base_sim = calcular_similitud_inteligente(texto_cv, f"{vacante_info['titulo']} {vacante_info['descripcion']}") / 100.0

    # 1. Formación profesional (máx 15)
    kw_formacion = ["universidad", "egresado", "titulado", "bachiller", "licenciado", "ingenier", "administracion", "instituto", "grado", "maestria", "diplomado", "estudiante"]
    hits_form = sum(1 for k in kw_formacion if k in texto_low)
    pts_formacion = min(15.0, round(8.0 + min(7.0, hits_form * 1.8), 1))

    # 2. Experiencia en puestos similares (máx 25)
    kw_exp = ["experiencia", "practica", "analista", "asistente", "coordinador", "especialista", "desarrollador", "funciones", "cargo", "empresa", "proyecto", "202"]
    hits_exp = sum(1 for k in kw_exp if k in texto_low)
    pts_experiencia = min(25.0, round((base_sim * 16.0) + min(9.0, hits_exp * 1.3), 1))

    # 3. Conocimientos técnicos (máx 25)
    pts_conocimientos = min(25.0, round(base_sim * 25.0, 1))

    # 4. Manejo de herramientas informáticas (máx 15)
    kw_herr = ["excel", "power bi", "sql", "python", "word", "office", "sap", "erp", "crm", "scrum", "git", "canva", "tableau", "linux", "cloud", "aws", "javascript", "html"]
    hits_herr = sum(1 for k in kw_herr if k in texto_low)
    pts_herramientas = min(15.0, round(6.5 + min(8.5, hits_herr * 1.7), 1))

    # 5. Logros y responsabilidades anteriores (máx 10)
    kw_logros = ["logr", "mejor", "optimiz", "increment", "reducc", "lider", "implement", "responsable", "meta", "kpi", "resultado", "%"]
    hits_logros = sum(1 for k in kw_logros if k in texto_low)
    pts_logros = min(10.0, round(4.5 + min(5.5, hits_logros * 1.4), 1))

    # 6. Estabilidad y trayectoria laboral (máx 10)
    anios = re.findall(r'20[12]\d', texto_low)
    pts_estabilidad = min(10.0, round(5.5 + min(4.5, len(set(anios)) * 1.1), 1))

    # Si hay API Key de OpenRouter activa, permitimos que el LLM refine la matriz JSON
    if OPENROUTER_API_KEY and not OPENROUTER_API_KEY.startswith("PEGA_"):
        prompt = f"""
        Evalúa este CV para el puesto '{vacante_info['titulo']}' ({vacante_info['descripcion']}) según esta matriz exacta:
        - formacion (máximo 15)
        - experiencia (máximo 25)
        - conocimientos (máximo 25)
        - herramientas (máximo 15)
        - logros (máximo 10)
        - estabilidad (máximo 10)
        Responde SOLO un JSON válido: {{"formacion":13,"experiencia":20,"conocimientos":21,"herramientas":13,"logros":8,"estabilidad":8}}
        CV: {texto_cv[:1600]}
        """
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": "meta-llama/llama-3.2-3b-instruct:free", "messages": [{"role": "user", "content": prompt}]},
                timeout=7
            )
            if r.ok and "choices" in r.json():
                contenido = r.json()["choices"][0]["message"]["content"]
                ini, fin = contenido.find("{"), contenido.rfind("}") + 1
                if ini != -1 and fin > ini:
                    d = json.loads(contenido[ini:fin])
                    pts_formacion = min(15.0, max(5.0, float(d.get("formacion", pts_formacion))))
                    pts_experiencia = min(25.0, max(8.0, float(d.get("experiencia", pts_experiencia))))
                    pts_conocimientos = min(25.0, max(8.0, float(d.get("conocimientos", pts_conocimientos))))
                    pts_herramientas = min(15.0, max(5.0, float(d.get("herramientas", pts_herramientas))))
                    pts_logros = min(10.0, max(3.0, float(d.get("logros", pts_logros))))
                    pts_estabilidad = min(10.0, max(3.0, float(d.get("estabilidad", pts_estabilidad))))
        except Exception:
            pass

    total_100 = round(pts_formacion + pts_experiencia + pts_conocimientos + pts_herramientas + pts_logros + pts_estabilidad, 1)

    if total_100 >= 80:
        clasificacion = "Candidato Prioritario (80 - 100 pts)"
    elif total_100 >= 65:
        clasificacion = "Candidato para Evaluar (65 - 79 pts)"
    else:
        clasificacion = "No Prioritario (< 65 pts)"

    return {
        "formacion": pts_formacion,
        "experiencia": pts_experiencia,
        "conocimientos": pts_conocimientos,
        "herramientas": pts_herramientas,
        "logros": pts_logros,
        "estabilidad": pts_estabilidad,
        "total_cv": total_100,
        "clasificacion": clasificacion
    }

def detectar_mejor_perfil_cv(texto_cv):
    mejor_item = VACANTES_INICIALES[0]
    mejor_puntaje = -1.0
    for item in VACANTES_INICIALES:
        puntaje = calcular_similitud_inteligente(texto_cv, f"{item['titulo']} {item['descripcion']}")
        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor_item = item
    return mejor_item

# ==========================================
# 5. BUSCADOR WEB INTERNO MULTIFUENTE (PARA LA AGENCIA)
# ==========================================
HEADERS_WEB = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SkillMatchAI/3.0"}

def fetch_getonboard(query_en):
    trabajos = []
    termino = query_en.split()[0] if query_en else "analyst"
    try:
        url = f"https://www.getonbrd.com/api/v0/search/jobs?query={quote_plus(termino)}&per_page=6"
        r = requests.get(url, headers=HEADERS_WEB, timeout=5)
        if r.ok:
            for item in r.json().get("data", []):
                attr = item.get("attributes", {})
                titulo = attr.get("title", "Vacante LATAM")
                desc = re.sub(r'<[^>]+>', ' ', attr.get("description", "") or attr.get("functions", ""))
                link = item.get("links", {}).get("public_url", "https://www.getonbrd.com")
                trabajos.append({
                    "portal": "GetOnBoard",
                    "titulo": titulo,
                    "empresa": "Empresa Verificada (📍 LATAM / Perú • 🌐 GetOnBoard)",
                    "link": link,
                    "descripcion": f"{titulo} {desc}"
                })
    except Exception:
        pass
    return trabajos

def fetch_themuse():
    trabajos = []
    try:
        r = requests.get("https://www.themuse.com/api/public/jobs?page=1", headers=HEADERS_WEB, timeout=5)
        if r.ok:
            for job in r.json().get("results", [])[:8]:
                titulo = job.get("name", "Vacante")
                empresa = job.get("company", {}).get("name", "Empresa")
                link = job.get("refs", {}).get("landing_page", "https://www.themuse.com/search")
                desc = re.sub(r'<[^>]+>', ' ', job.get("contents", ""))
                trabajos.append({
                    "portal": "The Muse",
                    "titulo": titulo,
                    "empresa": f"{empresa} (📍 Global / Remoto • 🌐 The Muse)",
                    "link": link,
                    "descripcion": f"{titulo} {desc}"
                })
    except Exception:
        pass
    return trabajos

def fetch_remotive(query_en):
    trabajos = []
    termino = query_en.split()[0] if query_en else ""
    try:
        r = requests.get(f"https://remotive.com/api/remote-jobs?search={quote_plus(termino)}&limit=8", headers=HEADERS_WEB, timeout=5)
        if r.ok:
            for job in r.json().get("jobs", [])[:8]:
                titulo = job.get("title", "Vacante")
                empresa = job.get("company_name", "Empresa")
                link = job.get("url", "https://remotive.com")
                desc = re.sub(r'<[^>]+>', ' ', job.get("description", ""))
                trabajos.append({
                    "portal": "Remotive",
                    "titulo": titulo,
                    "empresa": f"{empresa} (📍 Remoto • 🌐 Remotive)",
                    "link": link,
                    "descripcion": f"{titulo} {desc}"
                })
    except Exception:
        pass
    return trabajos

def generar_portales_peru(perfil_optimo):
    titulo_rol = perfil_optimo["titulo"]
    desc_rol = perfil_optimo["descripcion"]
    slug = perfil_optimo["slug_pe"]
    return [
        {
            "portal": "LinkedIn Perú",
            "titulo": f"{titulo_rol} (Empresas en Perú)",
            "empresa": "Empresas Corporativas (📍 Lima / Perú • 🌐 LinkedIn Jobs)",
            "link": f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(titulo_rol)}&location=Peru",
            "descripcion": f"{titulo_rol} {desc_rol}"
        },
        {
            "portal": "Bumeran Perú",
            "titulo": f"Convocatoria: {titulo_rol}",
            "empresa": "Empresas Nacionales (📍 Perú • 🌐 Bumeran.com.pe)",
            "link": f"https://www.bumeran.com.pe/empleos-busqueda-{slug}.html",
            "descripcion": f"{titulo_rol} {desc_rol}"
        }
    ]

# ==========================================
# 6. ENDPOINTS DE LA PLATAFORMA (CANDIDATO E INTERNO B2B)
# ==========================================
app = FastAPI(title="SkillMatch AI Headhunting API", description="Motor B2B con Matriz de 100 Puntos y Entrevista IA")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class EntrevistaPayload(BaseModel):
    candidato_id: int
    respuestas_competencias: str
    respuesta_caso_practico: str
    referencias_verificadas: bool = True

class ConvocatoriaEmpresaPayload(BaseModel):
    empresa: str
    titulo: str
    formacion: str
    experiencia: str
    conocimientos: str
    herramientas: str
    competencias: str
    resultado_esperado: str

@app.get("/")
def home():
    return {"mensaje": "Servidor de SkillMatch AI Headhunting activo en Render"}

# PASO 1 DEL POSTULANTE Y DE LA AGENCIA: PROCESAR CV CON MATRIZ A.1
@app.post("/procesar-cv/")
async def procesar_cv(archivo_cv: UploadFile = File(...)):
    if not archivo_cv.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Debe ser un archivo PDF")

    session = Session()
    try:
        contenido_archivo = await archivo_cv.read()
        texto_cv, correos_en_links = extraer_datos_pdf(contenido_archivo)

        if not texto_cv:
            raise HTTPException(status_code=400, detail="El PDF está vacío o es una imagen escaneada. Súbelo con texto seleccionable.")

        correo_real = detectar_correo_infalible(texto_cv, correos_en_links)
        nombre_real = archivo_cv.filename.replace(".pdf", "").replace("_", " ")

        nuevo_estudiante = Estudiante(
            nombres=nombre_real,
            correo=correo_real,
            carrera="Evaluado por IA",
            texto_cv=texto_cv
        )
        session.add(nuevo_estudiante)
        session.commit()

        asegurar_catalogo_vacantes(session)
        todas_las_vacantes = session.query(Vacante).all()

        # Evaluar contra todas las empresas aliadas (SOLO VISIBLE PARA LA AGENCIA INTERNA)
        ranking_vacantes = []
        for vac in todas_las_vacantes:
            if "wikipedia" in vac.titulo.lower():
                continue
            puntaje = calcular_similitud_inteligente(nuevo_estudiante.texto_cv, f"{vac.titulo} {vac.descripcion}")
            ranking_vacantes.append({
                "id": vac.id,
                "titulo": vac.titulo,
                "empresa": vac.empresa.nombre if vac.empresa else "Empresa con Contrato",
                "descripcion": vac.descripcion,
                "match_score": puntaje
            })

        ranking_vacantes.sort(key=lambda x: x["match_score"], reverse=True)
        mejor_vacante = ranking_vacantes[0]
        perfil_catalogo = detectar_mejor_perfil_cv(texto_cv)

        # Calcular Matriz A.1 (100 puntos)
        matriz_a1 = evaluar_matriz_a1(texto_cv, mejor_vacante)
        mejor_vacante["match_score"] = matriz_a1["total_cv"]

        nuevo_match = Match(
            id_vacante=mejor_vacante["id"],
            id_estudiante=nuevo_estudiante.id,
            porcentaje=matriz_a1["total_cv"],
            desbloqueado=False
        )
        session.add(nuevo_match)
        session.commit()

        # Guardar en memoria interna para cuando complete la entrevista A.2 y decisión A.3
        EVALUACIONES_AGENCIA[nuevo_estudiante.id] = {
            "candidato_id": nuevo_estudiante.id,
            "nombre": nombre_real,
            "correo": correo_real,
            "puesto_optimo": mejor_vacante["titulo"],
            "empresa_destino": mejor_vacante["empresa"],
            "matriz_a1": matriz_a1,
            "puntaje_entrevista": 0.0,
            "puntaje_caso": 0.0,
            "puntaje_referencias": 85.0,
            "decision_final_a3": round(matriz_a1["total_cv"] * 0.25, 1),
            "estado_proceso": "1. CV Filtrado (Pendiente Entrevista A.2)"
        }

        return {
            "candidato_id": nuevo_estudiante.id,
            "nombre_candidato": nombre_real,
            "correo_extraido": correo_real,
            "area_detectada": perfil_catalogo["area_caso"],
            "preguntas_entrevista": PREGUNTAS_ENTREVISTA_A2,
            "caso_practico": perfil_catalogo["caso_practico"],
            # Datos exclusivos para el panel interno de la agencia (ocultos al postulante)
            "match_score": matriz_a1["total_cv"],
            "matriz_a1": matriz_a1,
            "mejor_vacante": mejor_vacante,
            "ranking_local": ranking_vacantes[:5]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

# PASO 2: ENTREVISTA TÉCNICA POR COMPETENCIAS + CASO PRÁCTICO (ÍTEM A.2 Y A.3)
@app.post("/evaluar-entrevista/")
async def evaluar_entrevista(payload: EntrevistaPayload):
    texto_comp = payload.respuestas_competencias.strip()
    texto_caso = payload.respuesta_caso_practico.strip()

    if len(texto_comp) < 15 or len(texto_caso) < 15:
        raise HTTPException(status_code=400, detail="Por favor responde con mayor detalle tanto las preguntas de experiencia como el caso práctico.")

    # Evaluar calidad de la entrevista técnica (0-100) y prueba práctica (0-100)
    palabras_comp = len(texto_comp.split())
    palabras_caso = len(texto_caso.split())

    kw_valor = ["resultado", "logr", "mejor", "sistem", "excel", "indicador", "kpi", "equipo", "client", "proces", "solucion", "analisis", "control", "estrategia", "datos"]
    hits_comp = sum(1 for k in kw_valor if k in texto_comp.lower())
    hits_caso = sum(1 for k in kw_valor if k in texto_caso.lower())

    score_entrevista = min(98.0, round(58.0 + min(25.0, palabras_comp * 0.45) + (hits_comp * 3.0), 1))
    score_caso = min(98.0, round(60.0 + min(24.0, palabras_caso * 0.45) + (hits_caso * 3.2), 1))
    score_referencias = 95.0 if payload.referencias_verificadas else 60.0

    # Si hay OpenRouter, pedimos evaluación experta del Caso Práctico
    if OPENROUTER_API_KEY and not OPENROUTER_API_KEY.startswith("PEGA_"):
        try:
            prompt = f"""
            Actúa como Director de Selección. Evalúa del 0 al 100:
            1) Entrevista por competencias: {texto_comp[:800]}
            2) Resolución del Caso Práctico: {texto_caso[:800]}
            Responde SOLO en JSON: {{"entrevista": 85, "caso_practico": 88}}
            """
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={"model": "meta-llama/llama-3.2-3b-instruct:free", "messages": [{"role": "user", "content": prompt}]},
                timeout=7
            )
            if r.ok and "choices" in r.json():
                c = r.json()["choices"][0]["message"]["content"]
                ini, fin = c.find("{"), c.rfind("}") + 1
                if ini != -1 and fin > ini:
                    d = json.loads(c[ini:fin])
                    score_entrevista = min(98.0, max(45.0, float(d.get("entrevista", score_entrevista))))
                    score_caso = min(98.0, max(45.0, float(d.get("caso_practico", score_caso))))
        except Exception:
            pass

    registro = EVALUACIONES_AGENCIA.get(payload.candidato_id, {})
    score_cv = registro.get("matriz_a1", {}).get("total_cv", 75.0)

    # FÓRMULA OFICIAL DEL DOCUMENTO (ÍTEM A.3):
    # CV y experiencia (25%) + Entrevista técnica (25%) + Prueba práctica (40%) + Referencias (10%)
    nota_final_a3 = round(
        (score_cv * 0.25) +
        (score_entrevista * 0.25) +
        (score_caso * 0.40) +
        (score_referencias * 0.10),
        1
    )

    if nota_final_a3 >= 80:
        veredicto = "APROBADO PRIORITARIO — Listo para presentar a Empresa con Contrato"
    elif nota_final_a3 >= 68:
        veredicto = "APTO EN RESERVA — Segunda validación recomendada"
    else:
        veredicto = "EN OBSERVACIÓN — No alcanza puntaje mínimo en prueba práctica"

    if payload.candidato_id in EVALUACIONES_AGENCIA:
        EVALUACIONES_AGENCIA[payload.candidato_id].update({
            "puntaje_entrevista": score_entrevista,
            "puntaje_caso": score_caso,
            "puntaje_referencias": score_referencias,
            "decision_final_a3": nota_final_a3,
            "veredicto_a3": veredicto,
            "estado_proceso": "2. Evaluación Completa (A.1 + A.2 + A.3)"
        })

    return {
        "mensaje_postulante": "Tus respuestas y tu caso práctico han sido registrados con éxito. Nuestro equipo evaluará tu desempeño y te contactará cuando tengamos la confirmación de la empresa para la firma de contrato.",
        "evaluacion_interna_a3": {
            "cv_25": score_cv,
            "entrevista_25": score_entrevista,
            "prueba_practica_40": score_caso,
            "referencias_10": score_referencias,
            "nota_final_ponderada": nota_final_a3,
            "veredicto": veredicto
        }
    }

# PASO 3: REGISTRAR PERFIL DE EMPRESA CON CONTRATO (ÍTEM B.1)
@app.post("/registrar-perfil-empresa/")
async def registrar_perfil_empresa(payload: ConvocatoriaEmpresaPayload):
    session = Session()
    try:
        empresa_obj = session.query(Empresa).filter_by(nombre=payload.empresa).first()
        if not empresa_obj:
            empresa_obj = Empresa(nombre=payload.empresa, plan_actual="ContratoExclusivo")
            session.add(empresa_obj)
            session.commit()

        descripcion_completa = (
            f"Formación: {payload.formacion}. Experiencia: {payload.experiencia}. "
            f"Conocimientos: {payload.conocimientos}. Herramientas: {payload.herramientas}. "
            f"Competencias: {payload.competencias}. Resultado esperado: {payload.resultado_esperado}."
        )
        nueva_vacante = Vacante(
            id_empresa=empresa_obj.id,
            titulo=payload.titulo,
            descripcion=descripcion_completa
        )
        session.add(nueva_vacante)
        session.commit()
        return {"mensaje": f"Convocatoria '{payload.titulo}' de {payload.empresa} registrada en el banco de datos interno."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

# PASO 4: EXPLORADOR WEB INTERNO (SOLO PARA USO DE LA AGENCIA)
@app.post("/explorar-trabajos-web/")
async def explorar_trabajos_web(archivo_cv: UploadFile = File(...)):
    if not archivo_cv.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Debe ser un archivo PDF")
    try:
        contenido = await archivo_cv.read()
        texto_cv, _ = extraer_datos_pdf(contenido)
        if not texto_cv:
            raise HTTPException(status_code=400, detail="El PDF está vacío o es una imagen.")

        perfil_optimo = detectar_mejor_perfil_cv(texto_cv)
        query_en = perfil_optimo["query_en"]

        bolsa_multifuente = generar_portales_peru(perfil_optimo)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futuros = [
                executor.submit(fetch_getonboard, query_en),
                executor.submit(fetch_themuse),
                executor.submit(fetch_remotive, query_en),
            ]
            for futuro in as_completed(futuros):
                try:
                    bolsa_multifuente.extend(futuro.result())
                except Exception:
                    pass

        evaluados = []
        for item in bolsa_multifuente:
            score = calcular_similitud_inteligente(texto_cv, item["descripcion"])
            evaluados.append({
                "portal": item["portal"],
                "titulo": item["titulo"],
                "empresa": item["empresa"],
                "link": item["link"],
                "match_score": score
            })
        evaluados.sort(key=lambda x: x["match_score"], reverse=True)

        top_diverso = []
        portales_usados = set()
        for oferta in evaluados:
            if oferta["portal"] not in portales_usados:
                top_diverso.append(oferta)
                portales_usados.add(oferta["portal"])
                if len(top_diverso) == 5:
                    break
        return {"top_5": top_diverso}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
