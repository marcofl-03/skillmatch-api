import os
import io
import re
import json
import unicodedata
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
import pdfplumber
import requests
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
    plan_actual = Column(String, default="PagoPorConexion")
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

# ==========================================
# 2. CATÁLOGO DE VACANTES EN BASE DE DATOS
# ==========================================
VACANTES_INICIALES = [
    {
        "empresa": "TechCorp Innovación",
        "titulo": "Desarrollador Backend Python / API",
        "query_en": "python backend developer",
        "slug_pe": "desarrollador-python-backend",
        "descripcion": "Desarrollo de aplicaciones backend con Python, FastAPI, Django o Flask. Manejo de bases de datos SQL, PostgreSQL, MySQL, creación de APIs REST, Git, algoritmos, programación orientada a objetos y resolución de problemas técnicos."
    },
    {
        "empresa": "DataMetrics Perú",
        "titulo": "Analista de Datos y Business Intelligence",
        "query_en": "data analyst business intelligence",
        "slug_pe": "analista-de-datos-power-bi",
        "descripcion": "Análisis de datos, creación de dashboards y reportes en Power BI, Tableau y Excel avanzado. Consultas en SQL, Python para ciencia de datos, estadística, KPIs, limpieza de datos, toma de decisiones y análisis comercial o financiero."
    },
    {
        "empresa": "PixelStudio Digital",
        "titulo": "Desarrollador Frontend & UI/UX",
        "query_en": "frontend developer ui ux",
        "slug_pe": "desarrollador-frontend-ux",
        "descripcion": "Diseño y desarrollo de interfaces web modernas con HTML, CSS, JavaScript, React, Vue o Bootstrap. Diseño responsivo, experiencia de usuario UX/UI, Figma, maquetación web, creatividad e integración con servicios web."
    },
    {
        "empresa": "Grupo Andino Corporativo",
        "titulo": "Analista de Gestión de Proyectos y Operaciones",
        "query_en": "project manager operations analyst",
        "slug_pe": "analista-de-proyectos-operaciones",
        "descripcion": "Gestión de proyectos, planificación estratégica, administración, ingeniería industrial o empresarial. Metodologías ágiles Scrum, Kanban, liderazgo de equipos, comunicación efectiva, optimización de procesos, Excel, atención al cliente y organización."
    },
    {
        "empresa": "GrowthLab LATAM",
        "titulo": "Especialista en Marketing Digital y Contenidos",
        "query_en": "digital marketing specialist",
        "slug_pe": "especialista-marketing-digital",
        "descripcion": "Gestión de campañas digitales, redes sociales, estrategia comercial, ventas, SEO, SEM, Google Ads, comunicación, redacción creativa, branding, diseño gráfico, Canva, análisis de mercado y fidelización de clientes."
    },
    {
        "empresa": "CloudNet Sistemas",
        "titulo": "Ingeniero de Soporte TI, Redes y Ciberseguridad",
        "query_en": "it support systems engineer",
        "slug_pe": "ingeniero-soporte-ti-sistemas",
        "descripcion": "Soporte técnico, administración de sistemas Windows y Linux, redes, hardware, software, seguridad informática, servidores en la nube AWS o Azure, mantenimiento de equipos, atención de incidencias y telecomunicaciones."
    }
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
# 3. EXTRACTOR AVANZADO DE TEXTO Y CORREO (MULTICAPA)
# ==========================================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

def extraer_datos_pdf(contenido_bytes):
    """Extrae todo el texto y también revisa enlaces/metadatos internos para no perder el correo."""
    texto_cv = ""
    correos_en_links = []

    with pdfplumber.open(io.BytesIO(contenido_bytes)) as pdf:
        for pagina in pdf.pages:
            # 1. Extraer texto con tolerancia mejorada para diseños en columnas (Canva/Word)
            t1 = pagina.extract_text(x_tolerance=2, y_tolerance=3) or ""
            t2 = pagina.extract_text(layout=True) or ""
            texto_cv += t1 + "\n" + t2 + "\n"

            # 2. Revisar si el correo está oculto en un hipervínculo (mailto:) del PDF
            for link in (pagina.hyperlinks or []):
                uri = str(link.get("uri", ""))
                if "@" in uri:
                    limpio = uri.replace("mailto:", "").split("?")[0].strip()
                    correos_en_links.append(limpio)

            for annot in (pagina.annots or []):
                uri = str(annot.get("uri", ""))
                if "@" in uri:
                    limpio = uri.replace("mailto:", "").split("?")[0].strip()
                    correos_en_links.append(limpio)

    return texto_cv.strip(), correos_en_links

def detectar_correo_infalible(texto_cv, correos_en_links):
    """Detecta el correo aunque tenga espacios fantasma, saltos de línea o letras separadas."""
    # Capa 1: Si estaba en un hipervínculo del PDF
    if correos_en_links:
        return correos_en_links[0].lower()

    # Capa 2: Limpiar espacios y saltos de línea alrededor de @ y de los puntos de dominio
    texto_limpio = re.sub(r'\s*@\s*', '@', texto_cv)
    texto_limpio = re.sub(r'\s*\.\s*(com|pe|edu|org|net|es|io|co)\b', r'.\1', texto_limpio, flags=re.IGNORECASE)

    patron = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    match = re.search(patron, texto_limpio)
    if match:
        return match.group(0).lower().strip('.')

    # Capa 3: Si Canva separó todas las letras con espacios (ej: "m a r c o @ g m a i l . c o m")
    for linea in texto_cv.splitlines():
        if "@" in linea:
            linea_compacta = re.sub(r'\s+', '', linea)
            match_compacto = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.(?:com|pe|edu|org|net|es|io|co)(?:\.pe)?', linea_compacta, flags=re.IGNORECASE)
            if match_compacto:
                return match_compacto.group(0).lower()

    # Capa 4: Pedirle a OpenRouter que extraiga el correo si el formato era muy atípico
    if OPENROUTER_API_KEY and not OPENROUTER_API_KEY.startswith("PEGA_"):
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "meta-llama/llama-3.2-3b-instruct:free",
                    "messages": [{"role": "user", "content": f"Extrae únicamente el correo electrónico que aparece en este CV. Si no hay ninguno responde NO: {texto_cv[:1500]}"}]
                },
                timeout=5
            )
            if r.ok and "choices" in r.json():
                resp = r.json()["choices"][0]["message"]["content"].strip()
                m_ia = re.search(patron, resp)
                if m_ia:
                    return m_ia.group(0).lower()
        except Exception:
            pass

    return "correo_no_detectado@mail.com"

# ==========================================
# 4. MOTOR DE IA Y ANÁLISIS SEMÁNTICO BILINGÜE
# ==========================================
STOPWORDS = {
    "para", "como", "con", "por", "una", "uno", "unos", "unas", "del", "las", "los",
    "que", "mas", "pero", "sus", "este", "esta", "estos", "estas", "entre", "sobre",
    "todo", "toda", "todos", "todas", "otro", "otra", "otros", "otras", "desde",
    "hasta", "donde", "cuando", "quien", "cual", "cuales", "sido", "estar", "tener",
    "hacer", "puede", "pueden", "trabajo", "empresa", "experiencia", "anos", "peru",
    "lima", "the", "and", "for", "with", "from", "that", "this", "are", "you", "our",
    "will", "have", "your", "work", "team", "role", "about", "looking"
}

SINONIMOS_BILINGUES = {
    "proyecto": ["project", "management", "scrum", "agile", "planning"],
    "proyectos": ["project", "projects", "management", "pmo", "agile"],
    "gestion": ["management", "manager", "operations", "planning", "strategy"],
    "operaciones": ["operations", "operational", "process", "logistics", "workflow"],
    "administracion": ["administration", "business", "management", "operations"],
    "procesos": ["process", "processes", "improvement", "optimization", "lean"],
    "liderazgo": ["leadership", "lead", "team", "coordinator", "management"],
    "datos": ["data", "analytics", "analysis", "database", "reporting", "bi"],
    "analista": ["analyst", "analysis", "analytics", "research", "specialist"],
    "desarrollador": ["developer", "engineer", "software", "programming", "coding"],
    "programacion": ["programming", "developer", "software", "code", "engineering"],
    "sistemas": ["systems", "system", "it", "technology", "infrastructure", "support"],
    "soporte": ["support", "helpdesk", "technical", "troubleshooting", "service"],
    "redes": ["network", "networking", "security", "cloud", "infrastructure"],
    "diseno": ["design", "designer", "ui", "ux", "frontend", "creative"],
    "ventas": ["sales", "commercial", "business", "account", "client", "growth"],
    "comercial": ["commercial", "sales", "marketing", "business", "customer"],
    "finanzas": ["finance", "financial", "accounting", "budget", "kpi"],
    "comunicacion": ["communication", "stakeholders", "client", "presentation"]
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
    if usar_llm and OPENROUTER_API_KEY and not OPENROUTER_API_KEY.startswith("PEGA_"):
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        prompt = f"""
        Actúa como un reclutador experto. Evalúa el siguiente CV contra la vacante y dime el porcentaje de compatibilidad.
        IMPORTANTE: Tu respuesta debe ser ÚNICAMENTE un número entre 0 y 100 (por ejemplo: 84).
        
        Vacante: {vacante_empresa[:1200]}
        CV: {perfil_estudiante[:1500]}
        """
        data = {
            "model": "meta-llama/llama-3.2-3b-instruct:free",
            "messages": [{"role": "user", "content": prompt}]
        }
        try:
            response = requests.post(url, headers=headers, json=data, timeout=7)
            resultado = response.json()
            if "choices" in resultado:
                respuesta_texto = resultado['choices'][0]['message']['content'].strip()
                numeros = re.findall(r'\d+(?:\.\d+)?', respuesta_texto)
                if numeros:
                    return round(min(98.0, max(15.0, float(numeros[0]))), 1)
        except Exception as e:
            print("Usando motor semántico bilingüe:", e)

    tokens_cv = expandir_tokens_bilingue(normalizar_texto(perfil_estudiante))
    tokens_vac = set(normalizar_texto(vacante_empresa))
    if not tokens_vac or not tokens_cv:
        return 40.0

    puntos = 0.0
    for tv in tokens_vac:
        if tv in tokens_cv:
            puntos += 1.0
        else:
            raiz = tv[:5] if len(tv) >= 5 else tv
            if len(raiz) >= 4 and any(tc.startswith(raiz) or raiz in tc for tc in tokens_cv):
                puntos += 0.65

    proporcion = puntos / max(6, min(35, len(tokens_vac)) * 0.42)
    score = 38.0 + (proporcion * 58.0)
    return round(min(96.0, max(25.0, score)), 1)

def detectar_mejor_perfil_cv(texto_cv):
    mejor_item = VACANTES_INICIALES[0]
    mejor_puntaje = -1.0
    for item in VACANTES_INICIALES:
        puntaje = calcular_similitud_inteligente(texto_cv, f"{item['titulo']} {item['descripcion']}", usar_llm=False)
        if puntaje > mejor_puntaje:
            mejor_puntaje = puntaje
            mejor_item = item
    return mejor_item

# ==========================================
# 5. MOTOR DE BÚSQUEDA WEB CON IA + MULTIFUENTE
# ==========================================
HEADERS_WEB = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SkillMatchAI/2.0"}

def buscar_con_ia_openrouter(texto_cv, perfil_optimo):
    """Intenta buscar en tiempo real con Perplexity Sonar en OpenRouter si la cuenta tiene créditos."""
    if not OPENROUTER_API_KEY:
        return []
    prompt = f"""
    Busca en internet 5 ofertas de trabajo activas en Perú o Remoto (LATAM) de distintos portales web para: {perfil_optimo['titulo']}.
    Resumen del candidato: {texto_cv[:900]}
    Responde ÚNICAMENTE con un arreglo JSON válido con URLs reales:
    [{{"titulo": "Puesto", "empresa": "Empresa (📍 Ubicación • 🌐 Portal)", "link": "https://...", "match_score": 88.5}}]
    """
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={"model": "perplexity/sonar", "messages": [{"role": "user", "content": prompt}]},
            timeout=10
        )
        if r.ok and "choices" in r.json():
            contenido = r.json()["choices"][0]["message"]["content"]
            ini, fin = contenido.find("["), contenido.rfind("]") + 1
            if ini != -1 and fin > ini:
                datos = json.loads(contenido[ini:fin])
                validos = [d for d in datos if str(d.get("link", "")).startswith("http")]
                if len(validos) >= 3:
                    return validos[:5]
    except Exception:
        pass
    return []

def fetch_getonboard(query_en):
    trabajos = []
    termino = query_en.split()[0] if query_en else "analyst"
    try:
        url = f"https://www.getonbrd.com/api/v0/search/jobs?query={quote_plus(termino)}&per_page=8"
        r = requests.get(url, headers=HEADERS_WEB, timeout=6)
        if r.ok:
            for item in r.json().get("data", []):
                attr = item.get("attributes", {})
                titulo = attr.get("title", "Vacante LATAM")
                desc = re.sub(r'<[^>]+>', ' ', attr.get("description", "") or attr.get("functions", ""))
                link = item.get("links", {}).get("public_url", "https://www.getonbrd.com")
                remoto = "Remoto / LATAM" if attr.get("remote") else "LATAM / Perú"
                trabajos.append({
                    "portal": "GetOnBoard",
                    "titulo": titulo,
                    "empresa": f"Empresa Verificada (📍 {remoto} • 🌐 GetOnBoard)",
                    "link": link,
                    "descripcion": f"{titulo} {desc}"
                })
    except Exception:
        pass
    return trabajos

def fetch_themuse():
    trabajos = []
    try:
        url = "https://www.themuse.com/api/public/jobs?page=1"
        r = requests.get(url, headers=HEADERS_WEB, timeout=6)
        if r.ok:
            for job in r.json().get("results", [])[:12]:
                titulo = job.get("name", "Vacante")
                empresa = job.get("company", {}).get("name", "Empresa")
                locs = job.get("locations", [])
                locacion = locs[0].get("name", "Global / Remoto") if locs else "Global / Remoto"
                link = job.get("refs", {}).get("landing_page", "https://www.themuse.com/search")
                desc = re.sub(r'<[^>]+>', ' ', job.get("contents", ""))
                trabajos.append({
                    "portal": "The Muse",
                    "titulo": titulo,
                    "empresa": f"{empresa} (📍 {locacion} • 🌐 The Muse)",
                    "link": link,
                    "descripcion": f"{titulo} {desc}"
                })
    except Exception:
        pass
    return trabajos

def fetch_jobicy():
    trabajos = []
    try:
        url = "https://jobicy.com/api/v2/remote-jobs?count=12"
        r = requests.get(url, headers=HEADERS_WEB, timeout=6)
        if r.ok:
            for job in r.json().get("jobs", []):
                titulo = job.get("jobTitle", "Vacante")
                empresa = job.get("companyName", "Empresa")
                locacion = job.get("jobGeo", "Remoto")
                link = job.get("url", "https://jobicy.com")
                desc = re.sub(r'<[^>]+>', ' ', job.get("jobDescription", "") or job.get("jobExcerpt", ""))
                trabajos.append({
                    "portal": "Jobicy",
                    "titulo": titulo,
                    "empresa": f"{empresa} (📍 {locacion} • 🌐 Jobicy)",
                    "link": link,
                    "descripcion": f"{titulo} {desc}"
                })
    except Exception:
        pass
    return trabajos

def fetch_arbeitnow():
    trabajos = []
    try:
        url = "https://www.arbeitnow.com/api/job-board-api"
        r = requests.get(url, headers=HEADERS_WEB, timeout=6)
        if r.ok:
            for job in r.json().get("data", [])[:12]:
                titulo = job.get("title", "Vacante")
                empresa = job.get("company_name", "Empresa")
                locacion = "Remoto" if job.get("remote") else job.get("location", "Global")
                link = job.get("url", "https://www.arbeitnow.com")
                desc = re.sub(r'<[^>]+>', ' ', job.get("description", ""))
                tags = " ".join(job.get("tags", []))
                trabajos.append({
                    "portal": "Arbeitnow",
                    "titulo": titulo,
                    "empresa": f"{empresa} (📍 {locacion} • 🌐 Arbeitnow)",
                    "link": link,
                    "descripcion": f"{titulo} {tags} {desc}"
                })
    except Exception:
        pass
    return trabajos

def fetch_remotive(query_en):
    trabajos = []
    termino = query_en.split()[0] if query_en else ""
    try:
        url = f"https://remotive.com/api/remote-jobs?search={quote_plus(termino)}&limit=12"
        r = requests.get(url, headers=HEADERS_WEB, timeout=6)
        if r.ok:
            for job in r.json().get("jobs", [])[:12]:
                titulo = job.get("title", "Vacante")
                empresa = job.get("company_name", "Empresa")
                locacion = job.get("candidate_required_location", "Remoto")
                link = job.get("url", "https://remotive.com")
                desc = re.sub(r'<[^>]+>', ' ', job.get("description", ""))
                trabajos.append({
                    "portal": "Remotive",
                    "titulo": titulo,
                    "empresa": f"{empresa} (📍 {locacion} • 🌐 Remotive)",
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
            "titulo": f"{titulo_rol} (Vacantes Activas)",
            "empresa": "Empresas en Perú (📍 Lima / Remoto • 🌐 LinkedIn Jobs)",
            "link": f"https://www.linkedin.com/jobs/search/?keywords={quote_plus(titulo_rol)}&location=Peru",
            "descripcion": f"{titulo_rol} {desc_rol}"
        },
        {
            "portal": "Bumeran Perú",
            "titulo": f"Convocatoria: {titulo_rol}",
            "empresa": "Corporaciones Nacionales (📍 Perú • 🌐 Bumeran.com.pe)",
            "link": f"https://www.bumeran.com.pe/empleos-busqueda-{slug}.html",
            "descripcion": f"{titulo_rol} {desc_rol}"
        }
    ]

# ==========================================
# 6. INICIALIZAR LA APP WEB Y PERMISOS (CORS)
# ==========================================
app = FastAPI(title="SkillMatch AI API", description="Motor de emparejamiento B2B")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"mensaje": "Servidor de SkillMatch AI activo en Render"}

# BOTÓN 1: ENCONTRAR EL TRABAJO MÁS ÓPTIMO EN LA BASE DE DATOS
@app.post("/procesar-cv/")
async def procesar_cv(archivo_cv: UploadFile = File(...)):
    if not archivo_cv.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Debe ser un archivo PDF")
    
    session = Session()
    try:
        contenido_archivo = await archivo_cv.read()
        texto_cv, correos_en_links = extraer_datos_pdf(contenido_archivo)
                    
        if not texto_cv:
            raise HTTPException(status_code=400, detail="No se pudo extraer texto del PDF.")
            
        correo_real = detectar_correo_infalible(texto_cv, correos_en_links)
        nombre_real = archivo_cv.filename.replace(".pdf", "").replace("_", " ")

        nuevo_estudiante = Estudiante(
            nombres=nombre_real, 
            correo=correo_real, 
            carrera="Por Definir", 
            texto_cv=texto_cv
        )
        session.add(nuevo_estudiante)
        session.commit()
        
        asegurar_catalogo_vacantes(session)
        todas_las_vacantes = session.query(Vacante).all()

        ranking_vacantes = []
        for vac in todas_las_vacantes:
            if "wikipedia" in vac.titulo.lower() or "web scraper" in (vac.empresa.nombre.lower() if vac.empresa else ""):
                continue
            puntaje = calcular_similitud_inteligente(nuevo_estudiante.texto_cv, f"{vac.titulo} {vac.descripcion}", usar_llm=False)
            ranking_vacantes.append({
                "id": vac.id,
                "titulo": vac.titulo,
                "empresa": vac.empresa.nombre if vac.empresa else "Empresa Asociada",
                "descripcion": vac.descripcion,
                "match_score": puntaje
            })

        ranking_vacantes.sort(key=lambda x: x["match_score"], reverse=True)
        mejor_vacante = ranking_vacantes[0]

        puntaje_final = calcular_similitud_inteligente(
            nuevo_estudiante.texto_cv,
            f"{mejor_vacante['titulo']}: {mejor_vacante['descripcion']}",
            usar_llm=True
        )
        if puntaje_final > mejor_vacante["match_score"]:
            mejor_vacante["match_score"] = puntaje_final

        nuevo_match = Match(
            id_vacante=mejor_vacante["id"],
            id_estudiante=nuevo_estudiante.id,
            porcentaje=mejor_vacante["match_score"],
            desbloqueado=False
        )
        session.add(nuevo_match)
        session.commit()
        
        return {
            "candidato_id": nuevo_estudiante.id,
            "correo_extraido": correo_real,
            "match_score": mejor_vacante["match_score"],
            "mejor_vacante": mejor_vacante,
            "ranking_local": ranking_vacantes[:5],
            "modelo_negocio": "Pago por contacto requerido para ver datos."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()

# BOTÓN 2: BUSCADOR WEB CON IA + MULTIFUENTE TOP 5
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

        # 1. Intentar primero con el motor de búsqueda IA de OpenRouter (Perplexity Sonar)
        ofertas_ia = buscar_con_ia_openrouter(texto_cv, perfil_optimo)
        if len(ofertas_ia) >= 5:
            return {"top_5": ofertas_ia[:5]}

        # 2. Consultar 6 portales web distintos en paralelo
        bolsa_multifuente = generar_portales_peru(perfil_optimo)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futuros = [
                executor.submit(fetch_getonboard, query_en),
                executor.submit(fetch_themuse),
                executor.submit(fetch_jobicy),
                executor.submit(fetch_arbeitnow),
                executor.submit(fetch_remotive, query_en),
            ]
            for futuro in as_completed(futuros):
                try:
                    bolsa_multifuente.extend(futuro.result())
                except Exception:
                    pass

        evaluados = []
        for item in bolsa_multifuente:
            score = calcular_similitud_inteligente(texto_cv, item["descripcion"], usar_llm=False)
            evaluados.append({
                "portal": item["portal"],
                "titulo": item["titulo"],
                "empresa": item["empresa"],
                "link": item["link"],
                "match_score": score
            })

        evaluados.sort(key=lambda x: x["match_score"], reverse=True)

        # Seleccionar la mejor oferta de CADA portal web diferente
        top_diverso = []
        portales_usados = set()
        for oferta in evaluados:
            if oferta["portal"] not in portales_usados:
                top_diverso.append({
                    "titulo": oferta["titulo"],
                    "empresa": oferta["empresa"],
                    "link": oferta["link"],
                    "match_score": oferta["match_score"]
                })
                portales_usados.add(oferta["portal"])
                if len(top_diverso) == 5:
                    break

        return {"top_5": top_diverso}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
