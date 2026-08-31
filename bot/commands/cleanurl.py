import json
import math
import re
from collections import Counter
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

import discord
from discord.ext import commands


def load_rules() -> dict:
    rules_path = "data/rules.json"
    try:
        with open(rules_path, "r") as f:
            data = json.load(f)
            print(f"Loaded {len(data.get('GENERAL', []))} general and {len(data) - 1} specific domain rules.")
            return data
    except FileNotFoundError:
        print(f"WARNING: {rules_path} not found. URL cleaner will have no rules.")
        return {"GENERAL": []}

def calculate_entropy(text: str) -> float:
    if not text:
        return 0.0
    entropy = 0
    length = len(text)
    counts = Counter(text)
    for count in counts.values():
        p_x = count / length
        entropy += - p_x * math.log2(p_x)
    return entropy

class CleanerCog(commands.Cog):
    def __init__(self, bot: discord.Bot):
        self.bot = bot
        self.rules = load_rules()

    def _filter(self, url: str) -> str:
        if url.startswith("www."):
            url = "https://" + url
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query, keep_blank_values=True)
        params_to_remove = {p.lower() for p in self.rules.get("GENERAL", [])}
        domain_parts = parsed_url.netloc.lower().split('.')
        for i in range(len(domain_parts)):
            current_domain = ".".join(domain_parts[i:])
            if current_domain in self.rules:
                params_to_remove.update({p.lower() for p in self.rules[current_domain]})
        filtered_params = {key: value for key, value in query_params.items() if key.lower() not in params_to_remove}
        final_params = {}
        for key, value_list in filtered_params.items():
            value_str = value_list[0]
            if isinstance(value_str, bytes):
                value_str = value_str.decode()
            if len(value_str) >= 20:
                entropy = calculate_entropy(value_str)
                if entropy >= 4:
                    continue
            final_params[key] = value_list
        new_query = urlencode(final_params, doseq=True)
        return urlunparse((parsed_url.scheme, parsed_url.netloc, parsed_url.path, parsed_url.params,
                           new_query, parsed_url.fragment))

    @commands.message_command(name="Remove URL trackers", integration_types={discord.IntegrationType.guild_install,
                                                                    discord.IntegrationType.user_install})
    async def clean_urls(self, ctx: discord.ApplicationContext, message: discord.Message):
        ephemeral = True if ctx.guild else False
        await ctx.defer(ephemeral=ephemeral)
        url_pattern = re.compile(r'https?://[^\s<>"]+|www\.[^\s<>"]+')
        found_urls = url_pattern.findall(message.content)
        if not found_urls:
            await ctx.followup.send("No URLs were found in this message")
            return
        cleaned_links = [self._filter(url) for url in found_urls]
        final_urls = "\n\n".join(f"<{link}>" for link in cleaned_links)
        await ctx.followup.send(f"{final_urls}\n-# This is in beta :)")

def setup(bot: discord.Bot):
    bot.add_cog(CleanerCog(bot))