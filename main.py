# main.py
# Applicazione FastAPI per generare preventivi PDF
# Per Replit: questo file sarà automaticamente eseguito

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT, TA_JUSTIFY
from reportlab.pdfgen import canvas
import io
import traceback
import requests
from datetime import datetime
import os
import uvicorn  # ← AGGIUNTO QUESTO IMPORT


app = FastAPI(
    title="Generatore Preventivi AbitaLab",
    description="Applicazione per generare preventivi professionali in formato PDF",
    version="2.0.0"
)

# --- Modelli Pydantic ---

class VocePreventivo(BaseModel):
    """Singola voce del preventivo"""
    descrizione: str
    pz: Optional[int] = None
    qta: Optional[float] = None
    um: Optional[str] = None
    prezzo: Optional[float] = None

class Posizione(BaseModel):
    """Posizione che contiene più voci"""
    numero: int
    voci: List[VocePreventivo]

class DatiCliente(BaseModel):
    """Dati del cliente"""
    nome: str
    indirizzo: str
    citta: str
    cantiere: str

class DatiAzienda(BaseModel):
    """Dati dell'azienda con valori predefiniti"""
    nome: str = "AbitaLab"
    indirizzo: str = "Via dell'Innovazione, 1"
    cap_citta: str = "20121 Milano (MI)"
    p_iva: str = "P.Iva: 12345678901"
    telefono: str = "Tel. 02 12345678"
    email: str = "Mail: info@abitalab.it"
    sito: str = "Sito: www.abitalab.it"

class Preventivo(BaseModel):
    """Modello principale del preventivo"""
    numero: str
    data: str
    cliente: DatiCliente
    azienda: DatiAzienda = DatiAzienda()
    posizioni: List[Posizione]
    iva_percentuale: float = 22.0

class PreventivoInput(BaseModel):
    """Input wrapper per compatibilità"""
    output: Preventivo

# --- Funzioni di utilità ---

def calcola_totale_voce(voce: VocePreventivo) -> float:
    """Calcola il totale per una singola voce"""
    if voce.prezzo is None:
        return 0.0
    
    # Se l'unità di misura contiene 'pz' (pezzi), usa il campo 'pz'
    if voce.um and 'pz' in voce.um.lower():
        if voce.pz is not None:
            return round(voce.prezzo * voce.pz, 2)
    # Se l'unità di misura è 'a corpo', usa solo il prezzo (quantità = 1)
    elif voce.um and 'corpo' in voce.um.lower():
        return round(voce.prezzo, 2)
    # Per metri quadri (mq), controlla se il prezzo sembra essere totale o unitario
    elif voce.um and 'mq' in voce.um.lower():
        if voce.qta is not None:
            # Se il prezzo è molto alto (>10.000€) e c'è una quantità significativa (>10mq),
            # probabilmente è un prezzo totale, non unitario
            if voce.prezzo > 10000 and voce.qta > 10:
                # Interpreta come prezzo totale
                return round(voce.prezzo, 2)
            else:
                # Interpreta come prezzo unitario per mq
                return round(voce.prezzo * voce.qta, 2)
    # Per tutte le altre unità di misura (ml, kg, ecc.), usa la quantità
    else:
        if voce.qta is not None:
            return round(voce.prezzo * voce.qta, 2)
    
    # Fallback: se non ci sono quantità definite, restituisce solo il prezzo
    return round(voce.prezzo, 2)

def formatta_euro(valore: float) -> str:
    """Formatta un valore in euro con separatori italiani"""
    return f"{valore:,.2f} €".replace(',', 'X').replace('.', ',').replace('X', '.')

def formatta_numero(valore: float) -> str:
    """Formatta un numero senza decimali se è intero, altrimenti con decimali"""
    if valore == int(valore):
        return str(int(valore))
    else:
        return f"{valore:.2f}".replace('.', ',')

def formatta_prezzo_e_um(voce: VocePreventivo) -> tuple:
    """Formatta prezzo e unità di misura"""
    if voce.prezzo is None:
        return '', voce.um if voce.um else ''
    
    # Formattazione normale
    prezzo_str = formatta_euro(voce.prezzo)
    um_str = voce.um if voce.um else ''
    return prezzo_str, um_str

# --- Canvas personalizzato per numerazione pagine ---

class PageNumCanvas(canvas.Canvas):
    """Canvas con numerazione pagine"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self.pages)
        for page in self.pages:
            self.__dict__.update(page)
            self.draw_page_number(page_count)
            super().showPage()
        super().save()

    def draw_page_number(self, page_count):
        self.setFont("Helvetica", 8)
        self.drawRightString(20*cm, 1*cm, f"Pagina {self._pageNumber} di {page_count}")

# --- Generazione PDF ---

def genera_pdf_preventivo(preventivo: Preventivo) -> io.BytesIO:
    """Genera il PDF del preventivo"""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4,
        rightMargin=1.5*cm, 
        leftMargin=1.5*cm,
        topMargin=1.5*cm, 
        bottomMargin=2*cm
    )

    # Stili
    styles = getSampleStyleSheet()
    
    # Stili personalizzati
    custom_styles = {
        'HeaderInfo': ParagraphStyle('HeaderInfo', fontSize=9, leading=12),
        'CompanyName': ParagraphStyle('CompanyName', fontName='Helvetica-Bold', fontSize=16, leading=18),
        'BoldRight': ParagraphStyle('BoldRight', fontName='Helvetica-Bold', alignment=TA_RIGHT),
        'SmallText': ParagraphStyle('SmallText', fontSize=8, leading=10),
        'SmallTextCenter': ParagraphStyle('SmallTextCenter', fontSize=8, leading=10, alignment=TA_CENTER),
        'SmallTextRight': ParagraphStyle('SmallTextRight', fontSize=8, leading=10, alignment=TA_RIGHT),
        'TotalsLabel': ParagraphStyle('TotalsLabel', alignment=TA_RIGHT, fontSize=10),
        'TotalsValue': ParagraphStyle('TotalsValue', fontName='Helvetica-Bold', alignment=TA_RIGHT, fontSize=10),
        'NormalCenter': ParagraphStyle('NormalCenter', alignment=TA_CENTER),
        'LegalTitle': ParagraphStyle('LegalTitle', fontName='Helvetica-Bold', fontSize=8, spaceBefore=6),
        'LegalBody': ParagraphStyle('LegalBody', fontSize=8, alignment=TA_JUSTIFY, spaceBefore=4)
    }
    
    for name, style in custom_styles.items():
        styles.add(style)

    elements = []

    # --- Intestazione ---
    # Caricamento logo
    logo_element = None
    try:
        logo_url = "https://www.abitalab.it/assets/custom/266/img/logo.png"
        response = requests.get(logo_url, timeout=10)
        if response.status_code == 200:
            logo_data = io.BytesIO(response.content)
            # Dimensioni ottimizzate per l'intestazione
            logo_element = Image(logo_data, width=7*cm, height=2*cm, hAlign='LEFT')
        else:
            # Fallback se il logo non si carica
            logo_element = Paragraph(
                f"<b>{preventivo.azienda.nome}</b>",
                ParagraphStyle('LogoFallback', fontSize=16, alignment=TA_CENTER)
            )
    except Exception as e:
        print(f"Errore caricamento logo: {e}")
        # Fallback in caso di errore
        logo_element = Paragraph(
            f"<b>{preventivo.azienda.nome}</b>",
            ParagraphStyle('LogoFallback', fontSize=16, alignment=TA_CENTER)
        )

    company_details = [
        Paragraph(preventivo.azienda.nome, styles['CompanyName']),
        Paragraph(preventivo.azienda.indirizzo, styles['HeaderInfo']),
        Paragraph(preventivo.azienda.cap_citta, styles['HeaderInfo']),
        Paragraph(preventivo.azienda.p_iva, styles['HeaderInfo']),
        Paragraph(preventivo.azienda.telefono, styles['HeaderInfo']),
        Paragraph(preventivo.azienda.email, styles['HeaderInfo']),
        Paragraph(preventivo.azienda.sito, styles['HeaderInfo']),
    ]

    header_table = Table([[logo_element, company_details]], colWidths=[7*cm, 11*cm])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, 0), 'LEFT'),
        ('LEFTPADDING', (1, 0), (1, 0), 20),  # Spazio tra logo e testo
        ('RIGHTPADDING', (0, 0), (0, 0), 15)  # Spazio a destra del logo
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 1*cm))

    # --- Informazioni preventivo e cliente ---
    quote_info = f"""
    Data: {preventivo.data}<br/><br/>
    <b>Preventivo N. {preventivo.numero}</b><br/><br/>
    <b>Cantiere:</b> {preventivo.cliente.cantiere}
    """
    
    client_info = f"""
    Spett.le<br/>
    <b>{preventivo.cliente.nome}</b><br/>
    {preventivo.cliente.indirizzo}<br/>
    {preventivo.cliente.citta}
    """

    info_table = Table(
        [[Paragraph(quote_info, styles['Normal']), Paragraph(client_info, styles['Normal'])]],
        colWidths=[9*cm, 9*cm]
    )
    info_table.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    elements.append(info_table)
    elements.append(Spacer(1, 1*cm))

    # --- Testo introduttivo ---
    elements.append(Paragraph(
        "RingraziandoVi per la gentile richiesta, siamo a sottoporre alla Vostra attenzione la nostra migliore offerta:",
        styles['Normal']
    ))
    elements.append(Spacer(1, 0.5*cm))

    # --- Tabella posizioni ---
    subtotale_merce = 0
    
    for pos in preventivo.posizioni:
        headers = ['Pos.', 'Descrizione', 'Pz', 'Qtà', 'U.M.', 'Prezzo', 'Totale']
        table_data = [headers]
        totale_posizione = 0
        
        is_first_row = True
        for voce in pos.voci:
            pos_num = Paragraph(f"<b>{pos.numero}</b>", styles['SmallTextCenter']) if is_first_row else ""
            
            totale_voce = calcola_totale_voce(voce)
            totale_posizione += totale_voce
            
            # Formattazione dati
            pz_str = str(voce.pz) if voce.pz is not None else ''
            qta_str = formatta_numero(voce.qta) if voce.qta is not None else ''
            prezzo_str, um_str = formatta_prezzo_e_um(voce)
            totale_str = formatta_euro(totale_voce)
            
            row = [
                pos_num,
                Paragraph(voce.descrizione, styles['SmallText']),
                Paragraph(pz_str, styles['SmallTextCenter']),
                Paragraph(qta_str, styles['SmallTextCenter']),
                Paragraph(um_str, styles['SmallTextCenter']),
                Paragraph(prezzo_str, styles['SmallTextRight']),
                Paragraph(totale_str, styles['SmallTextRight'])
            ]
            table_data.append(row)
            is_first_row = False
        
        subtotale_merce += totale_posizione
        
        # Riga totale posizione
        totale_pos_formatted = f"<b>{formatta_euro(totale_posizione)}</b>"
        table_data.append([
            '', 
            Paragraph("<b>Totale Posizione</b>", styles['BoldRight']), 
            '', '', '', '', 
            Paragraph(totale_pos_formatted, styles['BoldRight'])
        ])

        # Creazione tabella
        table = Table(
            table_data, 
            colWidths=[1*cm, 8.5*cm, 0.8*cm, 1.2*cm, 1.2*cm, 2.65*cm, 2.65*cm],
            repeatRows=1
        )
        
        table.setStyle(TableStyle([
            # Header
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003366')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            
            # Contenuto
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (2, 1), (4, -1), 'CENTER'),
            ('ALIGN', (5, 1), (-1, -1), 'RIGHT'),
            
            # Bordi
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.black),
            
            # Span per numero posizione (tutte le righe della posizione tranne l'ultima)
            ('SPAN', (0, 1), (0, -2)) if len(pos.voci) > 1 else None,
            # Span per totale posizione
            ('SPAN', (1, -1), (5, -1)),
            ('LINEABOVE', (0, -1), (-1, -1), 1, colors.black),
        ]))
        
        elements.append(table)
        elements.append(Spacer(1, 0.5*cm))

    # --- Totali finali ---
    imponibile = subtotale_merce
    iva = imponibile * (preventivo.iva_percentuale / 100.0)
    totale_finale = imponibile + iva

    summary_data = [
        [Paragraph('Imponibile', styles['TotalsLabel']), 
         Paragraph(formatta_euro(imponibile), styles['TotalsValue'])],
        [Paragraph(f'IVA {preventivo.iva_percentuale:.0f}%', styles['TotalsLabel']), 
         Paragraph(formatta_euro(iva), styles['TotalsValue'])],
        [Paragraph('<b>Totale Preventivo</b>', styles['TotalsLabel']), 
         Paragraph(f"<b>{formatta_euro(totale_finale)}</b>", styles['TotalsValue'])]
    ]
    
    summary_table = Table(summary_data, colWidths=[14*cm, 4*cm])
    summary_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LINEBELOW', (0, 0), (-1, 1), 0.5, colors.grey),
        ('LINEABOVE', (0, 2), (-1, 2), 1, colors.black),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
    ]))
    
    elements.append(summary_table)
    elements.append(Spacer(1, 2*cm))

    # --- Firma ---
    elements.append(Paragraph("Timbro e Firma del Cliente per Accettazione", styles['NormalCenter']))
    elements.append(Spacer(1, 0.5*cm))
    
    signature_line = Table([['']], colWidths=[8*cm])
    signature_line.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (0, 0), 1, colors.black),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER')
    ]))
    elements.append(signature_line)
    elements.append(PageBreak())

    # --- Condizioni generali ---
    legal_elements = [
        Paragraph("Condizioni Generali", styles['LegalTitle']),
        Paragraph("<b>Validità dell'offerta:</b> La presente offerta è valida per 30 giorni dalla data di emissione.", styles['LegalBody']),
        Paragraph("<b>Condizioni di pagamento:</b> Le modalità di pagamento saranno concordate in fase di contratto definitivo.", styles['LegalBody']),
        Paragraph("<b>Tempi di consegna:</b> I tempi di consegna sono indicativi e verranno confermati al momento dell'ordine definitivo.", styles['LegalBody']),
        Paragraph("<b>Note:</b> Eventuali opere non esplicitamente menzionate in questo preventivo sono da considerarsi escluse.", styles['LegalBody']),
        Spacer(1, 1*cm),
        Paragraph("Trattamento dei dati personali", styles['LegalTitle']),
        Paragraph("Ai sensi del Reg. UE n. 679/2016 (GDPR), i dati personali forniti saranno trattati per le finalità connesse all'esecuzione del presente rapporto pre-contrattuale e contrattuale.", styles['LegalBody']),
    ]
    elements.extend(legal_elements)

    # Costruzione PDF
    doc.build(elements, canvasmaker=PageNumCanvas)
    buffer.seek(0)
    return buffer

# --- Endpoints ---

@app.get("/", response_class=HTMLResponse)
async def home():
    """Pagina principale con interfaccia web"""
    html_content = """
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Generatore Preventivi AbitaLab</title>
        <style>
            body { font-family: Arial, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; }
            .header { text-align: center; margin-bottom: 30px; }
            .card { background: #f5f5f5; padding: 20px; border-radius: 8px; margin-bottom: 20px; }
            .btn { background: #003366; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; }
            .btn:hover { background: #004080; }
            .example-btn { background: #28a745; }
            .example-btn:hover { background: #218838; }
            .api-docs { background: #17a2b8; }
            .api-docs:hover { background: #138496; }
            pre { background: #f8f9fa; padding: 15px; border-radius: 4px; overflow-x: auto; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>🏗️ Generatore Preventivi AbitaLab</h1>
            <p>Sistema professionale per la generazione di preventivi in formato PDF</p>
        </div>

        <div class="grid">
            <div class="card">
                <h2>🚀 Test Rapido</h2>
                <p>Genera subito un preventivo di esempio per vedere il risultato:</p>
                <button class="btn example-btn" onclick="generateExample()">Genera Preventivo di Esempio</button>
            </div>

            <div class="card">
                <h2>📚 Documentazione API</h2>
                <p>Accedi alla documentazione interattiva Swagger:</p>
                <button class="btn api-docs" onclick="window.open('/docs', '_blank')">Apri Documentazione</button>
            </div>
        </div>

        <div class="card">
            <h2>📝 Struttura Dati JSON</h2>
            <p>Esempio della struttura JSON richiesta per generare un preventivo:</p>
            <pre><code>{
  "output": {
    "numero": "1017/2025",
    "data": "30/07/2025",
    "cliente": {
      "nome": "MARIO ROSSI",
      "indirizzo": "VIA ROMA 123",
      "citta": "20121 Milano (MI)",
      "cantiere": "VIA ROMA 123"
    },
    "azienda": {
      "nome": "AbitaLab",
      "indirizzo": "Via dell'Innovazione, 1",
      "cap_citta": "20121 Milano (MI)",
      "p_iva": "P.Iva: 12345678901",
      "telefono": "Tel. 02 12345678",
      "email": "Mail: info@abitalab.it",
      "sito": "Sito: www.abitalab.it"
    },
    "posizioni": [
      {
        "numero": 1,
        "voci": [
          {
            "descrizione": "Costruzione edificio residenziale",
            "pz": 1,
            "qta": 1,
            "um": "a corpo",
            "prezzo": 200000.00
          }
        ]
      }
    ],
    "iva_percentuale": 22.0
  }
}</code></pre>
        </div>

        <div class="card">
            <h2>🔧 Endpoints Disponibili</h2>
            <ul>
                <li><strong>POST /genera-preventivo</strong> - Genera PDF da dati JSON</li>
                <li><strong>POST /genera-esempio</strong> - Genera preventivo di esempio</li>
                <li><strong>GET /docs</strong> - Documentazione Swagger</li>
                <li><strong>GET /health</strong> - Verifica stato servizio</li>
            </ul>
        </div>

        <script>
            function generateExample() {
                window.open('/genera-esempio', '_blank');
            }
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/genera-preventivo", tags=["Preventivi"])
@app.post("/genera-preventivo/", tags=["Preventivi"])
async def genera_preventivo_endpoint(data: PreventivoInput):
    """Genera un preventivo PDF dai dati JSON forniti"""
    try:
        preventivo = data.output
        pdf_buffer = genera_pdf_preventivo(preventivo)
        filename = f"preventivo_{preventivo.numero.replace('/', '-')}.pdf"
        headers = {"Content-Disposition": f"inline; filename={filename}"}
        return StreamingResponse(pdf_buffer, media_type="application/pdf", headers=headers)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Errore nella generazione del PDF: {str(e)}")

@app.post("/genera-esempio", tags=["Preventivi"])
@app.post("/genera-esempio/", tags=["Preventivi"])
async def genera_esempio():
    """Genera un preventivo di esempio con dati predefiniti"""
    esempio = {
        "output": {
            "numero": "1017/2025",
            "data": datetime.now().strftime("%d/%m/%Y"),
            "cliente": {
                "nome": "MARIO ROSSI",
                "indirizzo": "VIA GARIBALDI 45",
                "citta": "20121 Milano (MI)",
                "cantiere": "VIA GARIBALDI 45 - RISTRUTTURAZIONE APPARTAMENTO"
            },
            "azienda": {
                "nome": "AbitaLab",
                "indirizzo": "Via dell'Innovazione, 1",
                "cap_citta": "20121 Milano (MI)",
                "p_iva": "P.Iva: 12345678901",
                "telefono": "Tel. 02 12345678",
                "email": "Mail: info@abitalab.it",
                "sito": "Sito: www.abitalab.it"
            },
            "posizioni": [
                {
                    "numero": 1,
                    "voci": [
                        {
                            "descrizione": "Costruzione base edificio residenziale",
                            "pz": None,
                            "qta": 300.0,
                            "um": "mq",
                            "prezzo": 800.00
                        },
                        {
                            "descrizione": "Maggiorazione tipologia villa singola",
                            "pz": 1,
                            "qta": 1,
                            "um": "a corpo",
                            "prezzo": 50000.00
                        },
                        {
                            "descrizione": "Maggiorazione 3 camere da letto e 2 bagni",
                            "pz": 1,
                            "qta": 1,
                            "um": "a corpo",
                            "prezzo": 25000.00
                        },
                        {
                            "descrizione": "Maggiorazione stile moderno",
                            "pz": 1,
                            "qta": 1,
                            "um": "a corpo",
                            "prezzo": 20000.00
                        },
                        {
                            "descrizione": "Impianto fotovoltaico 6kW",
                            "pz": 1,
                            "qta": 1,
                            "um": "a corpo",
                            "prezzo": 15000.00
                        },
                        {
                            "descrizione": "Riscaldamento a pavimento",
                            "pz": None,
                            "qta": 300.0,
                            "um": "mq",
                            "prezzo": 45.00
                        }
                    ]
                }
            ],
            "iva_percentuale": 22.0
        }
    }
    
    preventivo_model = PreventivoInput(**esempio)
    return await genera_preventivo_endpoint(preventivo_model)

@app.get("/health", tags=["Sistema"])
async def health_check():
    """Verifica lo stato dell'applicazione"""
    return {
        "status": "OK",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "service": "Generatore Preventivi AbitaLab"
    }



if __name__ == '__main__':
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        # reload only useful during local dev; disable in prod
    )
