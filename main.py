import os
import io
import re
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
# 2. MOTOR DE IA CON OPENROUTER
# ==========================================

# ⚠️ REEMPLAZA ESTO CON TU LLAVE REAL DE OPENROUTER
OPENROUTER_API_KEY = "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"


def calcular_similitud(perfil_estudiante, vacante_empresa):
    """Calcula el porcentaje de match usando OpenRouter."""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    prompt = f"""
    Actúa como un reclutador experto. Evalúa el siguiente CV contra la vacante y dime el porcentaje de compatibilidad. 
    IMPORTANTE: Tu respuesta debe ser ÚNICAMENTE un número entre 0 y 100 (por ejemplo: 85). No agregues texto, ni símbolos, ni el signo de porcentaje.

    Vacante: {vacante_empresa[:1500]}
    CV: {perfil_estudiante[:1500]}
    """

    data = {
        "model": "meta-llama/llama-3.2-3b-instruct:free",
        "messages": [{"role": "user", "content": prompt}]
    }

    try:
        response = requests.post(url, headers=headers, json=data, timeout=15)
        resultado = response.json()
        respuesta_texto = resultado['choices'][0]['message']['content'].strip()
        numeros = re.findall(r'\d+(?:\.\d+)?', respuesta_texto)
        return round(min(100.0, max(0.0, float(numeros[0]))), 2) if numeros else 0.0
    except Exception as e:
        print("Error con OpenRouter:", e)
        return 0.0


# ==========================================
# 3. INICIALIZAR LA APP WEB Y PERMISOS (CORS)
# ==========================================
app = FastAPI(title="SkillMatch AI API", description="Motor de emparejamiento B2B")

# Permiso obligatorio para que Hostinger pueda comunicarse con Render
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"mensaje": "Servidor de SkillMatch AI activo en Render"}


# BOTÓN 1: MATCH CON BASE LOCAL
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
            carrera="Pendiente",
            texto_cv=texto_cv.strip()
        )
        session.add(nuevo_estudiante)
        session.commit()

        vacante_activa = session.query(Vacante).order_by(Vacante.id.desc()).first()
        if not vacante_activa:
            nueva_empresa = Empresa(nombre="TechCorp Innovación")
            session.add(nueva_empresa)
            session.commit()
            vacante_activa = Vacante(
                id_empresa=nueva_empresa.id,
                titulo="Practicante de Desarrollo Backend",
                descripcion="Buscamos un practicante con sólidos conocimientos en Python y SQL. Capacidad para resolver problemas y buena comunicación."
            )
            session.add(vacante_activa)
            session.commit()

        score = calcular_similitud(nuevo_estudiante.texto_cv, vacante_activa.descripcion)

        return {
            "candidato_id": nuevo_estudiante.id,
            "match_score": score,
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

        url_api = "https://remotive.com/api/remote-jobs?category=software-dev"
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

                score = calcular_similitud(texto_cv, desc_limpia)
                resultados.append({
                    "titulo": titulo,
                    "empresa": f"{empresa} (📍 {locacion_real})",
                    "link": enlace,
                    "match_score": score
                })

                # Evaluamos las primeras 5 vacantes compatibles para responder rápido
                if len(resultados) >= 5:
                    break

        top_5 = sorted(resultados, key=lambda x: x['match_score'], reverse=True)[:5]
        return {"top_5": top_5}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))