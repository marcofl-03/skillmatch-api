# Usar una versión de Python oficial y ligera
FROM python:3.10-slim

# Establecer el directorio de trabajo
WORKDIR /app

# Copiar el archivo de dependencias e instalarlas
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto de los archivos del proyecto
COPY . .

# Hugging Face Spaces expone el puerto 7860 por defecto
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]