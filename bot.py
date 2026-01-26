import os
import logging
import json
import re
from datetime import datetime
from io import BytesIO
import tempfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from google.cloud import vision
from google.oauth2 import service_account
import pandas as pd

# Configuration du logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

# Catégories de frais
CATEGORIES = [
    "Repas professionnels",
    "Carburant/Déplacements",
    "Matériel médical",
    "Fournitures",
    "Formations",
    "Téléphone/Internet",
    "Autres"
]

# Structure de données des frais (simulé avec stockage en mémoire)
# En production, utiliser une vraie base de données
frais_data = []

# Client Google Vision
vision_client = None

def init_vision_client():
    """Initialise le client Google Vision"""
    global vision_client
    try:
        if not GOOGLE_CREDENTIALS_JSON:
            logger.error("GOOGLE_APPLICATION_CREDENTIALS vide!")
            return
        
        # Charger les credentials depuis le JSON en variable d'environnement
        logger.info("Chargement des credentials Google Vision...")
        credentials_info = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        vision_client = vision.ImageAnnotatorClient(credentials=credentials)
        logger.info("Google Vision initialisé avec succès ✓")
    except json.JSONDecodeError as e:
        logger.error(f"Erreur parsing JSON credentials: {e}")
    except Exception as e:
        logger.error(f"Erreur initialisation Google Vision: {e}")
        import traceback
        logger.error(traceback.format_exc())

def extract_text_from_image(image_bytes):
    """Extrait le texte d'une image avec Google Vision OCR"""
    try:
        image = vision.Image(content=image_bytes)
        response = vision_client.text_detection(image=image)
        texts = response.text_annotations
        
        if texts:
            return texts[0].description
        return None
    except Exception as e:
        logger.error(f"Erreur OCR: {e}")
        return None

def parse_ticket_info(text):
    """Parse les informations du ticket (montant, date)"""
    info = {
        'montant': None,
        'date': None,
        'texte_complet': text
    }
    
    # Recherche du montant (formats: 12.50€, 12,50€, 12.50, €12.50)
    montant_patterns = [
        r'(\d+[.,]\d{2})\s*€',
        r'€\s*(\d+[.,]\d{2})',
        r'total[:\s]+(\d+[.,]\d{2})',
        r'(\d+[.,]\d{2})\s*eur',
    ]
    
    for pattern in montant_patterns:
        match = re.search(pattern, text.lower())
        if match:
            montant_str = match.group(1).replace(',', '.')
            info['montant'] = float(montant_str)
            break
    
    # Recherche de la date (formats: JJ/MM/AAAA, JJ-MM-AAAA, etc.)
    date_patterns = [
        r'(\d{2})[/-](\d{2})[/-](\d{4})',
        r'(\d{2})[/-](\d{2})[/-](\d{2})',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                jour, mois, annee = match.groups()
                if len(annee) == 2:
                    annee = '20' + annee
                info['date'] = f"{jour}/{mois}/{annee}"
                break
            except:
                continue
    
    # Si pas de date trouvée, utiliser la date du jour
    if not info['date']:
        info['date'] = datetime.now().strftime("%d/%m/%Y")
    
    return info

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /start"""
    welcome_message = """
💼 *Assistant Frais Professionnels SF*

Je t'aide à gérer tes frais pros en quelques secondes !

*Comment ça marche ?*
📸 Envoie-moi simplement la photo de ton ticket
🤖 J'extrais automatiquement le montant et la date
📁 Je te demande la catégorie
✅ C'est enregistré !

*Commandes utiles :*
/recap - Voir tes frais du mois
/export - Télécharger l'Excel
/stats - Statistiques par catégorie
/help - Aide détaillée

*Catégories disponibles :*
• Repas professionnels
• Carburant/Déplacements
• Matériel médical
• Fournitures
• Formations
• Téléphone/Internet
• Autres

Envoie ta première photo de ticket ! 📸
"""
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /help"""
    help_text = """
*Guide d'utilisation* 📖

*Ajouter un frais :*
1. Prends en photo ton ticket
2. Envoie la photo dans le chat
3. Je détecte automatiquement montant et date
4. Choisis la catégorie
5. Confirme ou modifie si besoin

*Consulter tes frais :*
`/recap` - Frais du mois en cours
`/recap 12` - Frais de décembre
`/stats` - Répartition par catégorie

*Exporter pour ton comptable :*
`/export` - Excel du mois en cours
`/export 2024` - Excel de toute l'année 2024

*Modifier/Supprimer :*
`/liste` - Voir tous les frais avec ID
`/supprimer 5` - Supprimer le frais #5

*Astuces :*
• Prends des photos nettes et bien éclairées
• Le ticket doit être bien visible
• Si je me trompe, tu peux corriger manuellement
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la réception d'une photo de ticket"""
    await update.message.reply_text("📸 Photo reçue ! Analyse en cours...")
    
    try:
        # Récupérer la photo en meilleure qualité
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        
        # Télécharger l'image
        image_bytes = await file.download_as_bytearray()
        
        # OCR avec Google Vision
        text = extract_text_from_image(bytes(image_bytes))
        
        if not text:
            await update.message.reply_text(
                "❌ Je n'ai pas réussi à lire le ticket. Essaie avec une photo plus nette !"
            )
            return
        
        # Parser les infos
        info = parse_ticket_info(text)
        
        # Stocker temporairement dans le contexte
        context.user_data['pending_frais'] = info
        
        # Créer les boutons de catégories
        keyboard = []
        for i in range(0, len(CATEGORIES), 2):
            row = []
            row.append(InlineKeyboardButton(CATEGORIES[i], callback_data=f"cat_{i}"))
            if i + 1 < len(CATEGORIES):
                row.append(InlineKeyboardButton(CATEGORIES[i+1], callback_data=f"cat_{i+1}"))
            keyboard.append(row)
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Message de confirmation
        msg = f"✅ *Ticket analysé !*\n\n"
        if info['montant']:
            msg += f"💰 Montant : *{info['montant']:.2f}€*\n"
        else:
            msg += f"💰 Montant : _Non détecté_\n"
        msg += f"📅 Date : {info['date']}\n\n"
        msg += "Choisis la catégorie :"
        
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Erreur traitement photo: {e}")
        await update.message.reply_text(
            "❌ Erreur lors de l'analyse. Réessaie ou contacte le support."
        )

async def handle_category_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la sélection de catégorie"""
    query = update.callback_query
    await query.answer()
    
    # Récupérer l'index de catégorie
    cat_index = int(query.data.split('_')[1])
    categorie = CATEGORIES[cat_index]
    
    # Récupérer les infos temporaires
    pending = context.user_data.get('pending_frais')
    if not pending:
        await query.edit_message_text("❌ Session expirée. Renvoie la photo du ticket.")
        return
    
    # Si montant non détecté, demander à l'utilisateur
    if not pending['montant']:
        context.user_data['pending_category'] = categorie
        await query.edit_message_text(
            f"💰 Je n'ai pas détecté le montant.\nEnvoie-le moi (exemple: 15.50)"
        )
        return
    
    # Enregistrer le frais
    frais = {
        'id': len(frais_data) + 1,
        'date': pending['date'],
        'montant': pending['montant'],
        'categorie': categorie,
        'timestamp': datetime.now().isoformat()
    }
    
    frais_data.append(frais)
    
    # Confirmation
    msg = f"✅ *Frais enregistré !*\n\n"
    msg += f"📁 Catégorie : {categorie}\n"
    msg += f"💰 Montant : {frais['montant']:.2f}€\n"
    msg += f"📅 Date : {frais['date']}\n\n"
    msg += f"_Total ce mois : {get_total_mois():.2f}€_"
    
    await query.edit_message_text(msg, parse_mode='Markdown')
    
    # Nettoyer les données temporaires
    context.user_data.pop('pending_frais', None)

async def handle_montant_manuel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère la saisie manuelle du montant"""
    if 'pending_category' not in context.user_data:
        return
    
    try:
        montant = float(update.message.text.replace(',', '.'))
        pending = context.user_data.get('pending_frais')
        categorie = context.user_data.get('pending_category')
        
        frais = {
            'id': len(frais_data) + 1,
            'date': pending['date'],
            'montant': montant,
            'categorie': categorie,
            'timestamp': datetime.now().isoformat()
        }
        
        frais_data.append(frais)
        
        msg = f"✅ *Frais enregistré !*\n\n"
        msg += f"📁 Catégorie : {categorie}\n"
        msg += f"💰 Montant : {montant:.2f}€\n"
        msg += f"📅 Date : {frais['date']}\n\n"
        msg += f"_Total ce mois : {get_total_mois():.2f}€_"
        
        await update.message.reply_text(msg, parse_mode='Markdown')
        
        context.user_data.pop('pending_frais', None)
        context.user_data.pop('pending_category', None)
        
    except ValueError:
        await update.message.reply_text("❌ Montant invalide. Utilise le format: 15.50")

def get_total_mois():
    """Calcule le total des frais du mois en cours"""
    mois_actuel = datetime.now().strftime("%m/%Y")
    total = sum(f['montant'] for f in frais_data if f['date'].endswith(mois_actuel))
    return total

async def recap_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /recap pour voir le récapitulatif"""
    if len(context.args) > 0:
        mois = context.args[0].zfill(2)
        annee = datetime.now().year
        filtre = f"{mois}/{annee}"
    else:
        filtre = datetime.now().strftime("%m/%Y")
    
    frais_mois = [f for f in frais_data if f['date'].endswith(filtre)]
    
    if not frais_mois:
        await update.message.reply_text(f"Aucun frais enregistré pour {filtre}")
        return
    
    msg = f"📊 *Récapitulatif {filtre}*\n\n"
    
    # Grouper par catégorie
    par_categorie = {}
    for frais in frais_mois:
        cat = frais['categorie']
        if cat not in par_categorie:
            par_categorie[cat] = []
        par_categorie[cat].append(frais)
    
    for cat, items in par_categorie.items():
        total_cat = sum(f['montant'] for f in items)
        msg += f"*{cat}* : {total_cat:.2f}€ ({len(items)} ticket{'s' if len(items) > 1 else ''})\n"
    
    total = sum(f['montant'] for f in frais_mois)
    msg += f"\n💰 *TOTAL : {total:.2f}€*"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /stats pour les statistiques"""
    if not frais_data:
        await update.message.reply_text("Aucun frais enregistré pour le moment.")
        return
    
    msg = "📈 *Statistiques par catégorie*\n\n"
    
    par_categorie = {}
    for frais in frais_data:
        cat = frais['categorie']
        par_categorie[cat] = par_categorie.get(cat, 0) + frais['montant']
    
    total_general = sum(par_categorie.values())
    
    for cat, montant in sorted(par_categorie.items(), key=lambda x: x[1], reverse=True):
        pourcentage = (montant / total_general * 100) if total_general > 0 else 0
        msg += f"• {cat}: {montant:.2f}€ ({pourcentage:.1f}%)\n"
    
    msg += f"\n💰 Total : {total_general:.2f}€"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /export pour générer l'Excel"""
    if not frais_data:
        await update.message.reply_text("Aucun frais à exporter.")
        return
    
    await update.message.reply_text("📄 Génération de l'Excel en cours...")
    
    # Filtrer par année si spécifié
    if len(context.args) > 0:
        annee = context.args[0]
        data_export = [f for f in frais_data if f['date'].endswith(annee)]
        filename = f"frais_pro_{annee}.xlsx"
    else:
        mois_actuel = datetime.now().strftime("%m/%Y")
        data_export = [f for f in frais_data if f['date'].endswith(mois_actuel)]
        filename = f"frais_pro_{datetime.now().strftime('%m_%Y')}.xlsx"
    
    # Créer le DataFrame
    df = pd.DataFrame(data_export)
    df = df[['date', 'categorie', 'montant']]
    df.columns = ['Date', 'Catégorie', 'Montant (€)']
    
    # Ajouter une ligne de total
    total_row = pd.DataFrame([['', 'TOTAL', df['Montant (€)'].sum()]], 
                            columns=df.columns)
    df = pd.concat([df, total_row], ignore_index=True)
    
    # Sauvegarder en Excel
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Frais professionnels')
    
    output.seek(0)
    
    await update.message.reply_document(
        document=output,
        filename=filename,
        caption=f"📊 Export Excel - {len(data_export)} frais - Total: {df['Montant (€)'].iloc[-1]:.2f}€"
    )

async def liste_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /liste pour voir tous les frais avec ID"""
    if not frais_data:
        await update.message.reply_text("Aucun frais enregistré.")
        return
    
    msg = "📋 *Liste des frais*\n\n"
    for frais in frais_data[-20:]:  # Derniers 20
        msg += f"#{frais['id']} - {frais['date']} - {frais['categorie']} - {frais['montant']:.2f}€\n"
    
    msg += f"\n_Utilise /supprimer ID pour supprimer un frais_"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def supprimer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Commande /supprimer pour supprimer un frais"""
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /supprimer ID\nEx: /supprimer 5")
        return
    
    try:
        frais_id = int(context.args[0])
        frais_to_remove = next((f for f in frais_data if f['id'] == frais_id), None)
        
        if frais_to_remove:
            frais_data.remove(frais_to_remove)
            await update.message.reply_text(f"✅ Frais #{frais_id} supprimé")
        else:
            await update.message.reply_text(f"❌ Frais #{frais_id} introuvable")
    except ValueError:
        await update.message.reply_text("❌ ID invalide")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestion des erreurs"""
    logger.error(f"Update {update} caused error {context.error}")

def main():
    """Fonction principale"""
    # Initialiser Google Vision
    init_vision_client()
    
    # Créer l'application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("recap", recap_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("export", export_command))
    application.add_handler(CommandHandler("liste", liste_command))
    application.add_handler(CommandHandler("supprimer", supprimer_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_montant_manuel))
    application.add_handler(CallbackQueryHandler(handle_category_selection))
    
    application.add_error_handler(error_handler)
    
    logger.info("Bot Frais Pro démarré!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
