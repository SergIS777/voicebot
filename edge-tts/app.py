from fastapi import FastAPI, Query
from fastapi.responses import Response
import edge_tts
import io

app = FastAPI(title="Edge TTS API")

VOICES = {
    'svetlana': 'ru-RU-SvetlanaNeural',
    'dmitry': 'ru-RU-DmitryNeural'
}

@app.get("/")
def root():
    return {"status": "ok", "voices": list(VOICES.keys())}

@app.get("/tts")
async def tts(
    text: str = Query(...),
    voice: str = Query(default="svetlana"),
    rate: str = Query(default="+0%"),
    pitch: str = Query(default="+0Hz")
):
    if voice not in VOICES:
        return {"error": f"Voice must be one of {list(VOICES.keys())}"}
    communicate = edge_tts.Communicate(text, VOICES[voice], rate=rate, pitch=pitch)
    buf = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    buf.seek(0)
    return Response(content=buf.read(), media_type="audio/mpeg")

@app.get("/health")
def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8201)
