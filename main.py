import asyncio
import aiohttp
import json
import re

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

@register("hotupdate", "wzken", "一个用于热更新AstrBot插件的插件", "0.1.0", "https://github.com/wzken/astrbot_plugin_hot_update")
class HotUpdatePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        logger.info("HotUpdatePlugin initialized.")
        self.updatable_plugins = [] # 用于存储可更新插件的列表

    @filter.command_group("update")
    def update_group(self):
        pass

    @update_group.command("list")
    async def list_updatable_plugins(self, event: AstrMessageEvent):
        '''列出所有可更新的AstrBot插件。'''
        logger.info("Received /update list command.")
        self.updatable_plugins = [] # 清空之前的列表

        all_stars = self.context.get_all_stars()
        if not all_stars:
            yield event.plain_result("未找到任何已加载的插件。")
            return

        response_messages = ["正在检查插件更新，请稍候..."]
        yield event.plain_result(response_messages[0])

        tasks = []
        for star_metadata in all_stars:
            if star_metadata.repo_url and "github.com" in star_metadata.repo_url:
                tasks.append(self._check_plugin_update(star_metadata))

        if not tasks:
            yield event.plain_result("没有插件提供有效的GitHub仓库地址，无法检查更新。")
            return

        results = await asyncio.gather(*tasks)

        updatable_count = 0
        for result in results:
            if result:
                self.updatable_plugins.append(result)
                updatable_count += 1

        if updatable_count > 0:
            response_messages = ["以下插件有可用更新："]
            for i, (star_metadata, latest_version) in enumerate(self.updatable_plugins):
                response_messages.append(f"{i+1}. {star_metadata.name} (当前版本: {star_metadata.version}, 最新版本: {latest_version})")
            response_messages.append("\n使用 /update up <编号> 来更新指定插件。")
            yield event.plain_result("\n".join(response_messages))
        else:
            yield event.plain_result("所有插件都已是最新版本。")

    async def _check_plugin_update(self, star_metadata):
        """检查单个插件是否有GitHub更新。"""
        repo_url = star_metadata.repo_url
        match = re.search(r"github\.com/([^/]+)/([^/]+)", repo_url)
        if not match:
            logger.warning(f"Invalid GitHub repo URL for {star_metadata.name}: {repo_url}")
            return None

        owner, repo = match.groups()
        if repo.endswith(".git"):
            repo = repo[:-4]

        api_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(api_url) as response:
                    response.raise_for_status()
                    data = await response.json()
                    latest_version = data.get("tag_name", "").lstrip('vV')

                    if latest_version and self._compare_versions(star_metadata.version, latest_version) < 0:
                        return (star_metadata, latest_version)
        except aiohttp.ClientError as e:
            logger.error(f"Error checking update for {star_metadata.name} ({repo_url}): {e}")
        except Exception as e:
            logger.error(f"Unexpected error checking update for {star_metadata.name} ({repo_url}): {e}")
        return None

    def _compare_versions(self, current_version: str, latest_version: str) -> int:
        """比较版本号，如果 current_version < latest_version 返回 -1，相等返回 0，大于返回 1。"""
        curr_parts = list(map(int, current_version.split('.')))
        latest_parts = list(map(int, latest_version.split('.')))

        for i in range(max(len(curr_parts), len(latest_parts))):
            curr_part = curr_parts[i] if i < len(curr_parts) else 0
            latest_part = latest_parts[i] if i < len(latest_parts) else 0
            if curr_part < latest_part:
                return -1
            elif curr_part > latest_part:
                return 1
        return 0

    @filter.permission_type(filter.PermissionType.ADMIN)
    @update_group.command("up")
    async def update_plugin_command(self, event: AstrMessageEvent, indices_str: str):
        '''更新并重载指定编号的插件。支持同时更新多个插件，例如：/update up 1 3 5'''
        if not self.updatable_plugins:
            yield event.plain_result("没有可更新的插件列表，请先使用 /update list 命令。")
            return

        # 解析输入的编号字符串
        try:
            indices = [int(i.strip()) for i in indices_str.split()]
            if not indices:
                raise ValueError("没有提供有效的编号。")
        except ValueError:
            yield event.plain_result("无效的编号格式。请提供一个或多个用空格分隔的数字，例如：/update up 1 3 5")
            return

        results_messages = []
        for index in indices:
            if not (1 <= index <= len(self.updatable_plugins)):
                results_messages.append(f"编号 {index} 无效。请输入 1 到 {len(self.updatable_plugins)} 之间的数字。")
                continue

            star_metadata, latest_version = self.updatable_plugins[index - 1]
            plugin_name = star_metadata.name
            target_repo_url = star_metadata.repo_url

            if not target_repo_url:
                results_messages.append(f"插件 '{plugin_name}' 没有提供仓库地址，无法更新。")
                continue

            results_messages.append(f"正在更新插件 '{plugin_name}' 到版本 {latest_version}...")
            logger.info(f"Attempting to update plugin '{plugin_name}' from {target_repo_url}.")

            try:
                plugin_manager = self.context._star_manager
                if not hasattr(plugin_manager, 'install_plugin'):
                    results_messages.append("错误：AstrBot插件管理器不支持 install_plugin 方法。请检查AstrBot版本或联系开发者。")
                    logger.error("AstrBot plugin manager does not have install_plugin method.")
                    continue

                await plugin_manager.install_plugin(repo_url=target_repo_url, proxy=None)

                results_messages.append(f"插件 '{plugin_name}' 已成功更新并重载到版本 {latest_version}。")
                logger.info(f"Plugin '{plugin_name}' successfully updated and reloaded.")

            except Exception as e:
                logger.error(f"Error updating plugin '{plugin_name}': {e}")
                results_messages.append(f"更新插件 '{plugin_name}' 失败: {e}")
        
        yield event.plain_result("\n".join(results_messages))

    async def terminate(self):
        logger.info("HotUpdatePlugin terminated.")
