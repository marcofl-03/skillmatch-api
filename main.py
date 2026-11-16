import os
import io
import re
import unicodedata
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
        "descripcion": "Desarrollo de aplicaciones backend con Python, FastAPI, Django o Flask. Manejo de bases de datos SQL, PostgreSQL, MySQL, creación de APIs REST, Git, algoritmos, programación orientada a objetos y resolución de problemas técnicos."
    },
    {
        "empresa": "DataMetrics Perú",
        "titulo": "Analista de Datos y Business Intelligence",
        "descripcion": "Análisis de datos, creación de dashboards y reportes en Power BI, Tableau y Excel avanzado. Consultas en SQL, Python para ciencia de datos, estadística, KPIs, limpieza de datos, toma de decisiones y análisis comercial o financiero."
    },
    {
        "empresa": "PixelStudio Digital",
        "titulo": "Desarrollador Frontend & UI/UX",
        "descripcion": "Diseño y desarrollo de interfaces web modernas con HTML, CSS, JavaScript, React, Vue o Bootstrap. Diseño responsivo, experiencia de usuario UX/UI, Figma, maquetación web, creatividad e integración con servicios web."
    },
    {
        "empresa": "Grupo Andino Corporativo",
        "titulo": "Analista de Gestión de Proyectos y Operaciones",
        "descripcion": "Gestión de proyectos, planificación estratégica, administración, ingeniería industrial o empresarial. Metodologías ágiles Scrum, Kanban, liderazgo de equipos, comunicación efectiva, optimización de procesos, Excel, atención al cliente y organización."
    },
    {
        "empresa": "GrowthLab LATAM",
        "titulo": "Especialista en Marketing Digital y Contenidos",
        "descripcion": "Gestión de campañas digitales, redes sociales, estrategia comercial, ventas, SEO, SEM, Google Ads, comunicación, redacción creativa, branding, diseño gráfico, Canva, análisis de mercado y fidelización de clientes."
    },
    {
        "empresa": "CloudNet Sistemas",
        "titulo": "Ingeniero de Soporte TI, Redes y Ciberseguridad",
        "descripcion": "Soporte técnico, administración de sistemas Windows y Linux, redes, hardware, software, seguridad informática, servidores en la nube AWS o Azure, mantenimiento de equipos, atención de incidencias y telecomunicaciones."
    }
]

def asegurar_catalogo_vacantes(session):
    """Garantiza que la base de datos tenga múltiples vacantes variadas para encontrar la óptima."""
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
# 3. MOTOR DE IA Y ANÁLISIS SEMÁNTICO
# ==========================================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

STOPWORDS = {
    "para", "como", "con", "por", "una", "uno", "unos", "unas", "del", "las", "los",
    "que", "mas", "pero", "sus", "este", "esta", "estos", "estas", "entre", "sobre",
    "todo", "toda", "todos", "todas", "otro", "otra", "otros", "otras", "desde",
    "hasta", "donde", "cuando", "quien", "cual", "cuales", "sido", "estar", "tener",
    "hacer", "puede", "pueden", "trabajo", "empresa", "experiencia", "anos", "peru",
    "lima", "the", "and", "for", "with", "from", "that", "this", "are", "you", "our"
}

def normalizar_texto(texto):
    texto_sin_acentos = ''.join(
        c for c in unicodedata.normalize('NFD', texto.lower())
        if unicodedata.category(c) != 'Mn'
    )
    palabras = re.findall(r'[a-z0-9+#]{3,}', texto_sin_acentos)
    return [p for p in palabras if p not in STOPWORDS]

def calcular_similitud_inteligente(perfil_estudiante, vacante_empresa, usar_llm=False):
    """Evalúa la compatibilidad combinando LLM (si está activo) y análisis de habilidades."""
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
            response = requests.post(url, headers=headers, json=data, timeout=8)
            resultado = response.json()
            if "choices" in resultado:
                respuesta_texto = resultado['choices'][0]['message']['content'].strip()
                numeros = re.findall(r'\d+(?:\.\d+)?', respuesta_texto)
                if numeros:
                    return round(min(98.0, max(15.0, float(numeros[0]))), 1)
        except Exception as e:
            print("Usando motor semántico local:", e)

    # Motor de coincidencia de raíces y habilidades clave
    tokens_cv = set(normalizar_texto(perfil_estudiante))
    tokens_vac = set(normalizar_texto(vacante_empresa))
    if not tokens_vac or not tokens_cv:
        return 40.0

    # Coincidencias exactas y por raíz (ej. "programacion" <-> "programador", "datos" <-> "data")
    puntos = 0.0
    for tv in tokens_vac:
        if tv in tokens_cv:
            puntos += 1.0
        else:
            raiz = tv[:5] if len(tv) >= 5 else tv
            if len(raiz) >= 4 and any(tc.startswith(raiz) or raiz in tc for tc in tokens_cv):
                puntos += 0.65

    proporcion = puntos / max(6, len(tokens_vac) * 0.45)
    score = 38.0 + (proporcion * 58.0)
    return round(min(97.0, max(25.0, score)), 1)

# ==========================================
# 4. INICIALIZAR LA APP WEB Y PERMISOS (CORS)
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
        texto_cv = ""
        with pdfplumber.open(io.BytesIO(contenido_archivo)) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if texto:
                    texto_cv += texto + " "
                    
        if not texto_cv.strip():
            raise HTTPException(status_code=400, detail="No se pudo extraer texto del PDF.")
            
        match_correo = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', texto_cv)
        correo_real = match_correo.group(0) if match_correo else "correo_no_detectado@mail.com"
        nombre_real = archivo_cv.filename.replace(".pdf", "").replace("_", " ")

        nuevo_estudiante = Estudiante(
            nombres=nombre_real, 
            correo=correo_real, 
            carrera="Por Definir", 
            texto_cv=texto_cv.strip()
        )
        session.add(nuevo_estudiante)
        session.commit()
        
        # Aseguramos que existan todas las vacantes del catálogo en la BD
        asegurar_catalogo_vacantes(session)
        todas_las_vacantes = session.query(Vacante).all()

        # Evaluamos el CV contra TODAS las vacantes de la base de datos
        ranking_vacantes = []
        for vac in todas_las_vacantes:
            # Ignorar vacantes viejas de prueba de Wikipedia si existieran
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

        # Ordenamos de mayor a menor compatibilidad
        ranking_vacantes.sort(key=lambda x: x["match_score"], reverse=True)
        mejor_vacante = ranking_vacantes[0]

        # Si hay llave de OpenRouter activa, refinamos el puntaje de la vacante ganadora con el LLM
        puntaje_final = calcular_similitud_inteligente(
            nuevo_estudiante.texto_cv,
            f"{mejor_vacante['titulo']}: {mejor_vacante['descripcion']}",
            usar_llm=True
        )
        if puntaje_final > mejor_vacante["match_score"]:
            mejor_vacante["match_score"] = puntaje_final

        # Guardamos el Match ganador en la base de datos
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

# BOTÓN 2: BUSCADOR WEB TOP 5 (FILTRO PERÚ / LATAM)
@app.post("/explorar-trabajos-web/")
async def explorar_trabajos_web(archivo_cv: UploadFile = File(...)):
    if not archivo_cv.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Debe ser un archivo PDF")
    
    try:
        contenido = await archivo_cv.read()
        texto_cv = ""
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if texto:
                    texto_cv += texto + " "
                    
        if not texto_cv.strip():
            raise HTTPException(status_code=400, detail="El PDF está vacío o es una imagen.")

        url_api = "https://remotive.com/api/remote-jobs?category=software-dev&limit=25"
        respuesta = requests.get(url_api, timeout=15)
        datos_api = respuesta.json()
        
        resultados = []
        zonas_peru = ["peru", "perú", "latam", "america", "worldwide"]
        
        for trabajo in datos_api.get("jobs", []):
            ubicacion = trabajo.get("candidate_required_location", "").lower()
            if any(zona in ubicacion for zona in zonas_peru):
                titulo = trabajo.get("title", "Vacante")
                empresa = trabajo.get("company_name", "Empresa")
                locacion_real = trabajo.get("candidate_required_location", "Remoto")
                enlace = trabajo.get("url", "#")
                desc_html = trabajo.get("description", "")
                desc_limpia = re.sub(r'<[^>]+>', ' ', desc_html)
                
                score = calcular_similitud_inteligente(texto_cv, f"{titulo} {desc_limpia}", usar_llm=False)
                resultados.append({
                    "titulo": titulo,
                    "empresa": f"{empresa} (📍 {locacion_real})",
                    "link": enlace,
                    "match_score": score
                })
            
        top_5 = sorted(resultados, key=lambda x: x['match_score'], reverse=True)[:5]
        return {"top_5": top_5}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
