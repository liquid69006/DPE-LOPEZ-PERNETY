"""
DPE Prospector v5 — Surveillance des nouveaux DPE · Paris 14e arrondissement
Email quotidien à 8h — avec ou sans nouveaux DPE
Source : API Open Data ADEME (data.ademe.fr)
"""

import os
import json
import math
import smtplib
import requests
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ══════════════════════════════════════════════════════════════
#  CONFIGURATION
# ══════════════════════════════════════════════════════════════

# Zones géographiques définies par polygones GPS
ZONES = {

    "Pernéty": {
        "codes_postaux": ["75014"],
        "polygone": [
            (48.840043061854686, 2.321736917425511),
            (48.838392892844865, 2.3171846913975287),
            (48.835718175152266, 2.313753880276977),
            (48.83328184927922,  2.3106406471650303),
            (48.83033077439143,  2.3070915611906457),
            (48.82784854494275,  2.3042415234419877),
            (48.82607225882916,  2.312640364604789),
            (48.82920577017754,  2.3173171472272713),
            (48.82759987427036,  2.320997777253268),
            (48.82803710566591,  2.321475141034881),
            (48.82708065703318,  2.325079583495352),
            (48.82791229473327,  2.326836058887409),
            (48.83549209060908,  2.323759950173155),
            (48.835428246388574, 2.323025561572564),
            (48.83594811839123,  2.3219447632563686),
            (48.83596635941623,  2.3217923429804728),
            (48.83561977880677,  2.321196518267868),
            (48.83646798499893,  2.320171145505583),
            (48.836969605558835, 2.3197554538448344),
            (48.837343537617215, 2.3195198952380167),
            (48.837452980618366, 2.320198858282538),
            (48.8375806638179,   2.320157289116594),
            (48.83767186590359,  2.3203374221694446),
            (48.83838323647507,  2.3224851624150347),
        ]
    },
}

EMAIL_EXPEDITEUR   = os.getenv("EMAIL_EXPEDITEUR", "")   # Login SMTP Brevo
EMAIL_FROM         = os.getenv("EMAIL_FROM", "alerte_dpe@outlook.com")  # Adresse affichée
EMAIL_DESTINATAIRE = os.getenv("EMAIL_DESTINATAIRE", "ybufferne@century21.fr")
EMAIL_CC           = os.getenv("EMAIL_CC",           "")
EMAIL_MOT_DE_PASSE = os.getenv("EMAIL_MOT_DE_PASSE", "")

CACHE_FILE = "dpe_cache.json"
JOURS_HISTORIQUE_INITIAL = 30

# ══════════════════════════════════════════════════════════════
#  API ADEME
# ══════════════════════════════════════════════════════════════

API_BASE = "https://data.ademe.fr/data-fair/api/v1/datasets/meg-83tjwtg8dyz4vv7h1dqe/lines"

CHAMPS = [
    "numero_dpe",
    "date_reception_dpe",
    "adresse_ban",
    "code_postal_ban",
    "nom_commune_ban",
    "_geopoint",
]

# ══════════════════════════════════════════════════════════════
#  GÉOGRAPHIE
# ══════════════════════════════════════════════════════════════

def point_dans_polygone(lat, lng, polygone) -> bool:
    """Ray casting algorithm — détermine si un point est dans un polygone."""
    n = len(polygone)
    dedans = False
    j = n - 1
    for i in range(n):
        lat_i, lng_i = polygone[i]
        lat_j, lng_j = polygone[j]
        if ((lng_i > lng) != (lng_j > lng)) and            (lat < (lat_j - lat_i) * (lng - lng_i) / (lng_j - lng_i) + lat_i):
            dedans = not dedans
        j = i
    return dedans

# ══════════════════════════════════════════════════════════════
#  CACHE
# ══════════════════════════════════════════════════════════════

def charger_cache() -> dict:
    if Path(CACHE_FILE).exists():
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    return {"derniere_verification": None, "dpe_vus": {}}


def sauvegarder_cache(cache: dict):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

# ══════════════════════════════════════════════════════════════
#  COLLECTE
# ══════════════════════════════════════════════════════════════

def recuperer_dpe_bruts(date_depuis: str) -> list:
    """
    Récupère tous les DPE du 69003 depuis date_depuis.
    Pagination par curseur, sans filtre de zone.
    """
    url = (
        f"{API_BASE}"
        f"?size=100"
        f"&code_postal_ban_eq=75014"
        f"&date_reception_dpe_gte={date_depuis}"
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DPE-Monitor/1.0)"}
    tous = []

    while url:
        try:
            r = requests.get(url, timeout=30, headers=headers)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            print(f"    ⚠️  Erreur API : {e}")
            break
        resultats = data.get("results", [])
        if not resultats:
            break
        tous.extend(resultats)
        url = data.get("next")

    return tous


def affecter_zone(dpe: dict) -> str:
    """Affecte un DPE à une zone selon ses coordonnées GPS."""
    geopoint = dpe.get("_geopoint", "")
    if not geopoint:
        return "Autre"
    try:
        lat, lng = map(float, geopoint.split(","))
    except Exception:
        return "Autre"

    for nom_zone, cfg in ZONES.items():
        if point_dans_polygone(lat, lng, cfg["polygone"]):
            return nom_zone
    return "Autre"

def formater_date(date_iso: str) -> str:
    try:
        return datetime.strptime(date_iso[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return date_iso

# ══════════════════════════════════════════════════════════════
#  EMAIL — AVEC DPE
# ══════════════════════════════════════════════════════════════

def generer_email_avec_dpe(resultats_par_zone: dict) -> tuple:
    total    = sum(len(v) for v in resultats_par_zone.values())
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")

    pills = ""
    for nom, dpes in resultats_par_zone.items():
        pills += (
            f'<span style="display:inline-block;background:rgba(255,255,255,0.18);'
            f'border:1px solid rgba(255,255,255,0.3);padding:4px 16px;'
            f'border-radius:20px;margin:3px;font-size:13px;">'
            f'<strong>{len(dpes)}</strong> · {nom}</span>'
        )

    # Tri par date croissante pour chaque zone
    for nom_zone in resultats_par_zone:
        resultats_par_zone[nom_zone].sort(key=lambda d: d.get("date_reception_dpe", ""))

    lignes = ""
    for nom_zone, dpes in resultats_par_zone.items():
        lignes += f"""
        <tr>
          <td colspan="4" style="padding:8px 12px 4px;background:#f8fafc;
              border-top:2px solid #e5e7eb;border-bottom:1px solid #e5e7eb;">
            <span style="font-size:11px;font-weight:700;letter-spacing:0.5px;
                color:#6b7280;text-transform:uppercase;">📍 {nom_zone}</span>
            <span style="font-size:11px;color:#9ca3af;margin-left:8px;">
              — {len(dpes)} DPE</span>
          </td>
        </tr>"""

        for dpe in dpes:
            num_dpe    = dpe.get("numero_dpe", "—")
            date_r     = formater_date(dpe.get("date_reception_dpe", ""))
            adresse    = dpe.get("adresse_ban", "Adresse inconnue")
            cp         = dpe.get("code_postal_ban", "")
            commune    = dpe.get("nom_commune_ban", "")
            lien       = f"https://observatoire-dpe-audit.ademe.fr/afficher-dpe/{num_dpe}"
            etiquette  = (dpe.get("etiquette_dpe") or "?").upper()
            renouvellement = bool(dpe.get("numero_dpe_remplace"))
            type_batiment = (dpe.get("type_batiment") or "").lower()
            adresse_encoded = adresse.replace(" ", "+").replace(",", "")

            # Surface
            surface = dpe.get("surface_habitable_logement") or dpe.get("surface_habitable_immeuble")
            surface_str = f"{int(surface)} m²" if surface else "—"

            # Étage et digicode depuis complement_adresse_logement
            complement = dpe.get("complement_adresse_logement") or ""
            etage_num = dpe.get("numero_etage_appartement")

            # Extraire étage depuis compl_ref_logement (champ le plus fiable)
            import re
            compl_ref = dpe.get("compl_ref_logement") or dpe.get("complement_adresse_logement") or ""
            etage_str = "—"
            if compl_ref:
                compl_up = compl_ref.upper()
                if any(x in compl_up for x in ["RDC", "REZ DE CHAUSSEE", "REZ-DE-CHAUSSEE"]):
                    etage_str = "RDC"
                else:
                    m = re.search(r"(\d+)\s*[Ee][Mm]?[Ee]?\s*[Ee][Tt][Aa][Gg][Ee]", compl_ref, re.IGNORECASE)
                    if m:
                        etage_str = f"{m.group(1)}e"
                    else:
                        m2 = re.search(r"[Ee][Tt][Aa][Gg][Ee]\s*:?\s*(\d+)", compl_ref, re.IGNORECASE)
                        if m2:
                            etage_str = f"{m2.group(1)}e"
            if etage_str == "—" and etage_num is not None:
                etage_str = "RDC" if etage_num == 0 else f"{etage_num}e"
            # Extraire digicode
            digicode_str = "—"
            if complement:
                import re
                m = re.search(r'[Dd]igicode' + r'\s*=?\s*:?\s*([^;]+)', complement)
                if m:
                    digicode_str = m.group(1).strip()
                elif re.search(r'\b\d{4,6}\b', complement):
                    m2 = re.search(r'\b(\d{4,6})\b', complement)
                    if m2:
                        digicode_str = m2.group(1)

            # Badge individuel / collectif
            if type_batiment == "immeuble":
                badge_type_bat = '<span style="display:inline-block;background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;">🏢 Collectif</span>'
            elif type_batiment in ["appartement", "maison"]:
                label = "🏠 Maison" if type_batiment == "maison" else "🏠 Appart."
                badge_type_bat = f'<span style="display:inline-block;background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;">{label}</span>'
            else:
                badge_type_bat = f'<span style="display:inline-block;background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;padding:3px 8px;border-radius:12px;font-size:11px;">{type_batiment or "?"}</span>'

            # Couleurs officielles DPE
            couleurs_dpe = {
                "A": "#009F6B", "B": "#51B748", "C": "#CADD43",
                "D": "#F5E800", "E": "#F0A800", "F": "#E4581B", "G": "#D7221F"
            }
            couleur_bg  = couleurs_dpe.get(etiquette, "#9ca3af")
            texte_color = "#fff" if etiquette in ["A","B","C","F","G"] else "#111"

            # Mise en évidence des G
            row_style = ""
            if etiquette == "G":
                row_style = "background:#fff5f5;"

            # Badge DPE
            badge_dpe = (
                f'<span style="display:inline-block;background:{couleur_bg};color:{texte_color};'
                f'font-weight:900;font-size:18px;width:36px;height:36px;line-height:36px;'
                f'text-align:center;border-radius:6px;">{etiquette}</span>'
            )
            if etiquette == "G":
                badge_dpe += '<div style="font-size:10px;color:#d7221f;font-weight:700;margin-top:3px;">⚠️ Passoire</div>'

            # Badge renouvellement
            if renouvellement:
                badge_renouv = '<span style="display:inline-block;background:#f0fdf4;color:#166534;border:1px solid #bbf7d0;padding:3px 8px;border-radius:12px;font-size:11px;font-weight:600;">🔄 Renouvellement</span>'
            else:
                badge_renouv = '<span style="display:inline-block;background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;padding:3px 8px;border-radius:12px;font-size:11px;">🆕 Premier</span>'

            lignes += f"""
        <tr style="{row_style}">
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;
              font-family:monospace;font-size:12px;color:#374151;white-space:nowrap;">
            {num_dpe}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;
              font-size:13px;color:#374151;white-space:nowrap;">
            {date_r}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;font-size:13px;">
            <a href="https://www.google.com/maps/search/?api=1&query={adresse_encoded}"
               style="font-weight:600;color:#111827;text-decoration:none;"
               title="Voir sur Google Maps">{adresse} 📍</a><br>
            <span style="color:#6b7280;font-size:12px;">{cp} {commune}</span>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;">
            {badge_dpe}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;">
            {badge_type_bat}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;">
            {badge_renouv}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;font-size:13px;color:#374151;white-space:nowrap;">
            {surface_str}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;font-size:13px;color:#374151;white-space:nowrap;">
            {etage_str}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;font-size:13px;color:#374151;white-space:nowrap;">
            {digicode_str}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #f3f4f6;text-align:center;">
            <a href="{lien}"
               style="display:inline-block;background:#1d4ed8;color:#fff;
                      text-decoration:none;padding:6px 16px;border-radius:6px;
                      font-size:12px;font-weight:600;white-space:nowrap;">
              Voir →
            </a>
          </td>
        </tr>"""

    sujet = f"🏠 {total} nouveau{'x' if total > 1 else ''} DPE · Paris 14e"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:100%;margin:16px auto;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,0.10);">
    <div style="background:linear-gradient(135deg,#0f2942 0%,#1d4ed8 100%);padding:24px 20px;color:#fff;">
      <div style="font-size:11px;opacity:0.55;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px;">DPE Prospector · Pernéty</div>
      <div style="font-size:28px;font-weight:700;margin-bottom:4px;">🏠 {total} nouveau{"x" if total > 1 else ""} DPE détecté{"s" if total > 1 else ""}</div>
      <div style="font-size:14px;opacity:0.75;margin-bottom:16px;">Diagnostics reçus dans ta zone de prospection</div>
      <div>{pills}</div>
      <div style="margin-top:14px;font-size:11px;opacity:0.4;">Généré le {date_str}</div>
    </div>
    <div style="background:#fff;">
      <table style="width:100%;border-collapse:collapse;">
        <thead>
          <tr style="background:#f8fafc;border-bottom:2px solid #e5e7eb;">
            <th style="padding:10px 12px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap;">N° DPE</th>
            <th style="padding:10px 12px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;white-space:nowrap;">Date</th>
            <th style="padding:10px 12px;text-align:left;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Adresse du bien</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">DPE</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Logement</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Type</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Surface</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Étage</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Digicode</th>
            <th style="padding:10px 12px;text-align:center;font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:0.5px;">Attestation</th>
          </tr>
        </thead>
        <tbody>{lignes}</tbody>
      </table>
    </div>
    <div style="background:#f8fafc;padding:12px 20px;border-top:1px solid #e5e7eb;text-align:center;">
      <p style="margin:0;font-size:11px;color:#9ca3af;">Données <a href="https://data.ademe.fr/datasets/dpe03existant" style="color:#6b7280;">ADEME Open Data</a> · Licence Etalab · Mise à jour en continu</p>
    </div>
  </div>
</body>
</html>"""

    return sujet, html


# ══════════════════════════════════════════════════════════════
#  EMAIL — AUCUN DPE
# ══════════════════════════════════════════════════════════════

def generer_email_vide() -> tuple:
    date_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
    hier     = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    sujet    = "📋 Aucun nouveau DPE · Paris 14e"

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
  <div style="max-width:600px;margin:32px auto;border-radius:12px;overflow:hidden;box-shadow:0 4px 32px rgba(0,0,0,0.10);">
    <div style="background:linear-gradient(135deg,#374151 0%,#6b7280 100%);padding:24px 20px;color:#fff;">
      <div style="font-size:11px;opacity:0.55;letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px;">DPE Prospector · Rapport quotidien</div>
      <div style="font-size:26px;font-weight:700;margin-bottom:6px;">📋 Aucun nouveau DPE</div>
      <div style="font-size:14px;opacity:0.75;">Zone surveillée : <strong>Paris 14e arrondissement</strong></div>
      <div style="margin-top:14px;font-size:11px;opacity:0.4;">Généré le {date_str}</div>
    </div>
    <div style="background:#fff;padding:36px;">
      <p style="margin:0 0 12px;font-size:15px;color:#374151;line-height:1.6;">
        Aucun nouveau DPE n'a été déposé sur la zone du <strong>3e arrondissement de Lyon</strong>
        depuis le dernier rapport (veille du <strong>{hier}</strong>).
      </p>
      <p style="margin:0;font-size:14px;color:#6b7280;line-height:1.6;">Le prochain rapport sera envoyé demain matin à 8h.</p>
    </div>
    <div style="background:#f8fafc;padding:12px 20px;border-top:1px solid #e5e7eb;text-align:center;">
      <p style="margin:0;font-size:11px;color:#9ca3af;">Données <a href="https://data.ademe.fr/datasets/dpe03existant" style="color:#6b7280;">ADEME Open Data</a> · Licence Etalab · Mise à jour en continu</p>
    </div>
  </div>
</body>
</html>"""

    return sujet, html


# ══════════════════════════════════════════════════════════════
#  ENVOI EMAIL
# ══════════════════════════════════════════════════════════════

def envoyer_email(sujet: str, html: str):
    with smtplib.SMTP("smtp-relay.brevo.com", 587) as s:
        s.ehlo()
        s.starttls()
        s.login(EMAIL_EXPEDITEUR, EMAIL_MOT_DE_PASSE)
        destinataires = [d for d in [EMAIL_DESTINATAIRE, EMAIL_CC] if d]
        for destinataire in destinataires:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = sujet
            msg["From"]    = EMAIL_FROM
            msg["To"]      = destinataire
            msg.attach(MIMEText(html, "html"))
            s.sendmail(EMAIL_EXPEDITEUR, destinataire, msg.as_string())
            print(f"   ✉️  Envoyé à {destinataire}")

# ══════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print(f"🔍 DPE Prospector v5 · {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 60)

    cache   = charger_cache()
    dpe_vus = cache.get("dpe_vus", {})

    # Fenêtre glissante de 30 jours — capture tous les DPE déposés tardivement
    # Le cache des N° DPE évite tout doublon dans la boîte mail
    date_depuis = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    if not cache.get("derniere_verification"):
        print(f"🆕 1ère exécution — remontée sur 30 jours")

    print(f"📅 Recherche depuis : {date_depuis}\n")

    # Récupération de tous les DPE du 69003
    print(f"📡 Récupération des DPE depuis l'API ADEME...")
    tous_dpe = recuperer_dpe_bruts(date_depuis)
    print(f"   → {len(tous_dpe)} DPE récupérés au total")

    # Affectation par zone et filtrage des doublons
    resultats_par_zone = {"Pernéty": []}

    for dpe in tous_dpe:
        num = dpe.get("numero_dpe")
        if not num or num in dpe_vus:
            continue
        zone = affecter_zone(dpe)
        if zone in resultats_par_zone:  # Ignorer les DPE hors zones
            resultats_par_zone[zone].append(dpe)

    # Tri par date croissante dans chaque zone
    for zone in resultats_par_zone:
        resultats_par_zone[zone].sort(key=lambda d: d.get("date_reception_dpe", ""))

    # Supprimer les zones vides
    resultats_par_zone = {k: v for k, v in resultats_par_zone.items() if v}

    for nom_zone, dpes in resultats_par_zone.items():
        print(f"📍 {nom_zone} : {len(dpes)} nouveau(x) DPE")

    for dpes in resultats_par_zone.values():
        for dpe in dpes:
            if dpe.get("numero_dpe"):
                dpe_vus[dpe["numero_dpe"]] = datetime.now().isoformat()

    cache["dpe_vus"]              = dpe_vus
    cache["derniere_verification"] = datetime.now().isoformat()
    sauvegarder_cache(cache)

    # ── Mise à jour data.json pour le tableau de bord GitHub Pages ──
    # Charger l'historique existant
    data_file = Path("data.json")
    if data_file.exists():
        with open(data_file, "r") as f:
            historique = json.load(f)
    else:
        historique = {"dpe": []}

    # Ajouter les nouveaux DPE avec leur zone
    num_existants = {d["numero_dpe"] for d in historique["dpe"]}
    for nom_zone, dpes in resultats_par_zone.items():
        for dpe in dpes:
            if dpe.get("numero_dpe") not in num_existants:
                # Extraire étage et digicode
                import re as _re
                complement = dpe.get("complement_adresse_logement") or ""
                etage_num = dpe.get("numero_etage_appartement")
                etage_str = "—"
                if complement:
                    compl_up = complement.upper()
                    if any(x in compl_up for x in ["RDC", "REZ DE CHAUSSEE", "REZ-DE-CHAUSSEE"]):
                        etage_str = "RDC"
                    else:
                        m = _re.search(r"(\d+)\s*[Ee][Mm]?[Ee]?\s*[Ee][Tt][Aa][Gg][Ee]", complement, _re.IGNORECASE)
                        if m:
                            etage_str = f"{m.group(1)}e"
                if etage_str == "—" and etage_num is not None:
                    etage_str = "RDC" if etage_num == 0 else f"{etage_num}e"

                digicode_str = "—"
                if complement:
                    m2 = _re.search(r"[Dd]igicode\s*=?\s*:?\s*([^;\n]+)", complement)
                    if m2:
                        digicode_str = m2.group(1).strip()

                historique["dpe"].append({
                    "numero_dpe": dpe.get("numero_dpe"),
                    "date_reception_dpe": dpe.get("date_reception_dpe"),
                    "adresse_ban": dpe.get("adresse_ban"),
                    "code_postal_ban": dpe.get("code_postal_ban"),
                    "nom_commune_ban": dpe.get("nom_commune_ban"),
                    "etiquette_dpe": dpe.get("etiquette_dpe"),
                    "type_batiment": dpe.get("type_batiment"),
                    "surface_habitable_logement": dpe.get("surface_habitable_logement"),
                    "surface_habitable_immeuble": dpe.get("surface_habitable_immeuble"),
                    "numero_dpe_remplace": dpe.get("numero_dpe_remplace"),
                    "zone": nom_zone,
                    "etage_str": etage_str,
                    "digicode_str": digicode_str,
                })

    # Trier par date décroissante
    historique["dpe"].sort(key=lambda d: d.get("date_reception_dpe") or "", reverse=True)
    historique["derniere_maj"] = datetime.now().isoformat()

    with open(data_file, "w") as f:
        json.dump(historique, f, ensure_ascii=False, indent=2)
    print(f"📊 data.json mis à jour ({len(historique['dpe'])} DPE au total)")

    total = sum(len(v) for v in resultats_par_zone.values())
    print(f"\n{'=' * 60}")

    if resultats_par_zone:
        sujet, html = generer_email_avec_dpe(resultats_par_zone)
        print(f"📧 Envoi : {total} nouveau(x) DPE")
    else:
        sujet, html = generer_email_vide()
        print(f"📧 Envoi rapport vide")

    envoyer_email(sujet, html)
    print("✅ Email envoyé !")
    print("=" * 60)


if __name__ == "__main__":
    main()
