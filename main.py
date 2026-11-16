import os
import io
import pdfplumber
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse, HTMLResponse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import requests
from bs4 import BeautifulSoup

# Reemplaza esto con el token real que copiaste en el Paso 1
HF_TOKEN = "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"
API_URL = "https://api-inference.huggingface.co/models/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
headers = {"Authorization": f"Bearer {HF_TOKEN}"}

def obtener_vectores(textos):
    # Le pide a Hugging Face que procese los textos a través de internet
    response = requests.post(API_URL, headers=headers, json={"inputs": textos, "options": {"wait_for_model": True}})
    return response.json()
import re


# ==========================================
# 1. INICIALIZAR LA APP WEB (NUEVO)
# ==========================================
app = FastAPI(title="SkillMatch AI API", description="Motor de emparejamiento B2B")


# ==========================================
# 2. CONFIGURACIÓN DE LA BASE DE DATOS
# ==========================================
motor = create_engine('sqlite:///skillmatch.db', echo=False)
Base = declarative_base()


class Empresa(Base):
    __tablename__ = 'empresas'
    id = Column(Integer, primary_key=True)
    nombre = Column(String)
    vacantes = relationship("Vacante", back_populates="empresa")


class Estudiante(Base):
    __tablename__ = 'estudiantes'
    id = Column(Integer, primary_key=True)
    nombres = Column(String)
    correo = Column(String)
    carrera = Column(String)
    texto_cv = Column(Text)
    matches = relationship("Match", back_populates="estudiante_rel")  # CORREGIDO


class Vacante(Base):
    __tablename__ = 'vacantes'
    id = Column(Integer, primary_key=True)
    id_empresa = Column(Integer, ForeignKey('empresas.id'))
    titulo = Column(String)
    descripcion = Column(Text)
    empresa = relationship("Empresa", back_populates="vacantes")
    matches = relationship("Match", back_populates="vacante_rel")  # CORREGIDO


class Match(Base):
    __tablename__ = 'matches'
    id = Column(Integer, primary_key=True)
    id_vacante = Column(Integer, ForeignKey('vacantes.id'))
    id_estudiante = Column(Integer, ForeignKey('estudiantes.id'))
    porcentaje = Column(Float)
    desbloqueado = Column(Boolean, default=False)

    # CORREGIDO
    vacante_rel = relationship("Vacante", back_populates="matches")
    estudiante_rel = relationship("Estudiante", back_populates="matches")


Base.metadata.create_all(motor)
Session = sessionmaker(bind=motor)
session = Session()


# ==========================================
# 3. FUNCIONES DEL NÚCLEO (NLP y Scraping)
# ==========================================
def calcular_similitud(perfil, vacante):
    # Pedimos los vectores a la API de Hugging Face
    vectores = obtener_vectores([perfil, vacante])

    # Verificamos que la API haya respondido correctamente
    if isinstance(vectores, list) and len(vectores) == 2:
        # Calculamos la similitud con scikit-learn
        similitud = cosine_similarity([vectores[0]], [vectores[1]])[0][0]
        score_final = similitud * 100
        return round(max(0, score_final), 2)
    else:
        # En caso de error de conexión, retornamos 0 para no romper la app
        print("Error de conexión con la IA:", vectores)
        return 0


def evaluar_match(perfil, vacante):
    """Evalúa la compatibilidad entre el perfil y la vacante.

    Si hay una clave API de Gemini configurada, intenta usarla para un análisis más
    avanzado. Si no existe o falla la llamada, usa una comparación TF-IDF local
    como respaldo determinista y sin dependencias externas.
    """
    perfil = perfil or ""
    vacante = vacante or ""

    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        try:
            payload = {
                "contents": [{
                    "parts": [{
                        "text": (
                            "Eres un reclutador experto. Analiza la compatibilidad entre "
                            "el perfil del candidato y la oferta laboral. Responde solo con "
                            "JSON con la forma {'match_score': <numero entre 0 y 100>, "
                            "'motivo': '<breve explicación>'}.\n\n"
                            f"PERFIL:\n{perfil}\n\nVACANTE:\n{vacante}"
                        )
                    }]
                }],
                "generationConfig": {"temperature": 0.2}
            }
            respuesta = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
                params={"key": api_key},
                json=payload,
                timeout=15,
            )
            respuesta.raise_for_status()
            contenido = respuesta.json()
            texto = contenido["candidates"][0]["content"]["parts"][0]["text"]
            texto = texto.strip()
            if texto.startswith("```"):
                texto = texto.replace("```json", "", 1).replace("```", "", 1).strip()
            import json
            datos = json.loads(texto)
            score = float(datos.get("match_score", 0))
            if 0 <= score <= 100:
                return {
                    "match_score": round(score, 2),
                    "motivo": datos.get("motivo", "Evaluado con Gemini"),
                    "modelo": "gemini",
                    "modelo_negocio": "Pago por contacto requerido para ver datos."
                }
        except Exception:
            pass

    score = calcular_similitud(perfil, vacante)
    return {
        "match_score": score,
        "motivo": "Compatibilidad calculada por similitud TF-IDF.",
        "modelo": "tfidf",
        "modelo_negocio": "Pago por contacto requerido para ver datos."
    }


def buscar_vacantes_en_web_bot():
    # Apuntamos a un portal de empleos de prueba para tecnología
    url_objetivo = "https://realpython.github.io/fake-jobs/"
    cabeceras = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        respuesta = requests.get(url_objetivo, headers=cabeceras)
        respuesta.raise_for_status()
        sopa = BeautifulSoup(respuesta.text, 'html.parser')
        
        # Encontramos todas las "tarjetas" de vacantes en la página
        tarjetas_trabajo = sopa.find_all("div", class_="card-content")
        
        # Para no llenar la base de datos de golpe, tomaremos la primera vacante de la lista
        primera_tarjeta = tarjetas_trabajo[0]
        
        # Extraemos los datos específicos: título, empresa y ubicación
        titulo_vacante = primera_tarjeta.find("h2", class_="title").text.strip()
        empresa_nombre = primera_tarjeta.find("h3", class_="company").text.strip()
        ubicacion = primera_tarjeta.find("p", class_="location").text.strip()
        
        # Armamos una descripción de puesto más realista
        descripcion_completa = f"Puesto tecnológico: {titulo_vacante}. Empresa: {empresa_nombre}. Modalidad/Ubicación: {ubicacion}. Se requiere experiencia en lenguajes de programación, resolución de problemas y trabajo en equipo."
        
        # 1. Verificamos si la empresa ya existe, si no, la creamos
        empresa_bot = session.query(Empresa).filter_by(nombre=empresa_nombre).first()
        if not empresa_bot:
            empresa_bot = Empresa(nombre=empresa_nombre)
            session.add(empresa_bot)
            session.commit()

        # 2. Guardamos la vacante vinculada a esa empresa
        nueva_vacante = Vacante(id_empresa=empresa_bot.id, titulo=titulo_vacante, descripcion=descripcion_completa)
        session.add(nueva_vacante)
        session.commit()
        
        return nueva_vacante
        
    except Exception as e:
        print(f"Error extrayendo datos de la web: {e}")
        return None


# ==========================================
# 4. RUTAS DE LA API WEB (ENDPOINTS - NUEVO)
# ==========================================

# Ruta para ver si el servidor está vivo
# Ruta que muestra la interfaz web HTML
@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    with open("index.html", "r", encoding="utf-8") as file:
        html_content = file.read()
    return html_content


# Ruta para que la empresa accione el bot de búsqueda
@app.get("/ejecutar-bot-vacantes")
def ejecutar_bot():
    vacante = buscar_vacantes_en_web_bot()
    if vacante:
        return {"estado": "éxito", "vacante_encontrada": vacante.titulo}
    return {"estado": "error", "mensaje": "No se pudo extraer de la web"}

# Ruta para que la empresa publique una vacante manualmente
@app.post("/publicar-vacante/")
async def publicar_vacante(
    titulo: str = Form(...),
    empresa: str = Form(...),
    descripcion: str = Form(...)
):
    # Verificamos si la empresa existe, si no, la creamos
    empresa_obj = session.query(Empresa).filter_by(nombre=empresa).first()
    if not empresa_obj:
        empresa_obj = Empresa(nombre=empresa)
        session.add(empresa_obj)
        session.commit()

    # Guardamos la nueva vacante
    nueva_vacante = Vacante(id_empresa=empresa_obj.id, titulo=titulo, descripcion=descripcion)
    session.add(nueva_vacante)
    session.commit()
    
    return {"mensaje": "Vacante publicada con éxito", "vacante_id": nueva_vacante.id}


# Ruta para subir un CV y obtener el Match al instante
# Ruta para subir un CV y obtener el Match al instante
@app.post("/procesar-cv/")
async def procesar_cv(archivo_cv: UploadFile = File(...)):
    if not archivo_cv.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Debe ser un archivo PDF")
    
    try:
        # 1. Leemos los bytes del archivo web primero
        contenido_archivo = await archivo_cv.read()
        
        # 2. Le pasamos los bytes a pdfplumber para una extracción perfecta
        texto_cv = ""
        with pdfplumber.open(io.BytesIO(contenido_archivo)) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if texto:
                    texto_cv += texto + " "
                    
        # ==========================================
        # PASO DE DEPURACIÓN (Míralo en la terminal)
        # ==========================================
        print("--- TEXTO EXTRAÍDO DEL PDF ---")
        print(texto_cv)
        print("------------------------------")
                    
        # 3. Validar si el PDF estaba vacío
        if not texto_cv.strip():
            return {"mensaje": "No se pudo extraer texto. Asegúrate de que el PDF no sea una imagen."}

        # 4. EXTRACCIÓN DE DATOS REALES (REGEX)
        texto_cv = texto_cv.replace("@ ", "@").replace(" @", "@")
        match_correo = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', texto_cv)
        correo_real = match_correo.group(0) if match_correo else "correo_no_detectado@mail.com"
        # 5. Limpiar el nombre del archivo
        nombre_real = archivo_cv.filename.replace(".pdf", "").replace("_", " ")

        # 6. Guardar estudiante en BD con sus datos verdaderos
        nuevo_estudiante = Estudiante(
            nombres=nombre_real, 
            correo=correo_real, 
            carrera="Pendiente", 
            texto_cv=texto_cv.strip()
        )
        session.add(nuevo_estudiante)
        session.commit()
        
        # 7. Comparar con la última vacante disponible
        vacante_activa = session.query(Vacante).order_by(Vacante.id.desc()).first()
        if not vacante_activa:
            return {"mensaje": "No hay vacantes para comparar"}
            
        resultado = evaluar_match(nuevo_estudiante.texto_cv, vacante_activa.descripcion)
        
        # 8. Devolver la respuesta a la página web (JSON)
        return {
            "candidato_id": nuevo_estudiante.id,
            "match_score": resultado["match_score"],
            "modelo_negocio": resultado["modelo_negocio"],
            "motivo": resultado.get("motivo", "")
        }
        
    except Exception as e:
        print(f"Error procesando PDF: {e}") 
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# CÓDIGO PARA ARRANCAR EL SERVIDOR
# ==========================================
if __name__ == "__main__":
    import uvicorn

    # Enciende el servidor web local en el puerto 8000
    print("Iniciando servidor web de SkillMatch AI...")
    uvicorn.run(app, host="127.0.0.1", port=8000)

# Ruta para que el reclutador vea la lista de candidatos guardados
@app.get("/lista-candidatos/")
def lista_candidatos():
    estudiantes = session.query(Estudiante).all()
    lista = []
    for est in estudiantes:
        lista.append({
            "nombre": est.nombres,
            "correo": est.correo
        })
    return {"candidatos": lista}


# ==========================================
# NUEVO: BUSCADOR WEB DE TRABAJOS (FILTRO PERÚ)
# ==========================================
@app.post("/explorar-trabajos-web/")
async def explorar_trabajos_web(archivo_cv: UploadFile = File(...)):
    if not archivo_cv.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Debe ser un archivo PDF")

    try:
        # 1. Leer el CV subido
        contenido = await archivo_cv.read()
        texto_cv = ""
        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            for pagina in pdf.pages:
                texto = pagina.extract_text()
                if texto:
                    texto_cv += texto + " "

        if not texto_cv.strip():
            return {"mensaje": "El PDF está vacío o es una imagen."}

        # 2. Conectarse a la API de empleos
        url_api = "https://remotive.com/api/remote-jobs?category=software-dev"
        respuesta = requests.get(url_api)
        datos_api = respuesta.json()

        resultados = []

        # 3. FILTRO PERÚ: La IA solo evaluará trabajos disponibles para peruanos
        for trabajo in datos_api.get("jobs", []):
            ubicacion = trabajo.get("candidate_required_location", "").lower()

            # Zonas compatibles con talento en Perú
            zonas_peru = ["peru", "perú", "latam", "america", "worldwide"]

            # Si la ubicación de la vacante incluye alguna de nuestras zonas, la procesamos
            if any(zona in ubicacion for zona in zonas_peru):
                titulo = trabajo.get("title", "Vacante")
                empresa = trabajo.get("company_name", "Empresa")
                locacion_real = trabajo.get("candidate_required_location", "Remoto")
                enlace = trabajo.get("url", "#")
                desc_html = trabajo.get("description", "")

                # Limpiamos el texto web de etiquetas HTML
                desc_limpia = re.sub(r'<[^>]+>', ' ', desc_html)

                # El motor neuronal calcula el match
                score = calcular_similitud(texto_cv, desc_limpia)

                resultados.append({
                    "titulo": titulo,
                    # Añadimos un pin visual para tu página web
                    "empresa": f"{empresa} (📍 {locacion_real})",
                    "link": enlace,
                    "match_score": score
                })

        # 4. Ordenar de mayor a menor y devolver el Top 5 exacto
        top_5 = sorted(resultados, key=lambda x: x['match_score'], reverse=True)[:5]

        return {"top_5": top_5}

    except Exception as e:
        print(f"Error en explorador web: {e}")
        raise HTTPException(status_code=500, detail=str(e))