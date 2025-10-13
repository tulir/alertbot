import json

import dateutil.parser
from aiohttp.web import Request, Response, json_response
from maubot import Plugin, MessageEvent
from maubot.handlers import web, command
from mautrix.errors.request import MForbidden

helpstring = f"""# Alertbot

To control the alertbot you can use the following commands:
* `!help`: To show this help
* `!ping`: To check if the bot is alive
* `!raw`: To toggle raw mode (where webhook data is not parsed but simply forwarded as copyable text)
* `!roomid`: To let the bot show you the current matrix room id
* `!url`: To let the bot show you the webhook url

More information is on [Github](https://github.com/moan0s/alertbot)
"""


def convert_slack_webhook_to_markdown(data):
    # Error Handling: Check if data is a dictionary
    if not isinstance(data, dict):
        return ["Input data must be a dictionary"]

    markdown_parts = []
    attachment_titles = []

    if "text" in data:
        markdown_parts.append(f"{data['text']}")

    if "attachments" in data:
        for attach in data["attachments"]:
            if "title" in attach:
                attachment_titles.append(attach['title'])
                title_md = f"## {attach['title']}" if "title_link" not in attach else f"[{attach['title']}]({attach['title_link']})"
                markdown_parts.append(f"> {title_md}")

            for key in ["text", "image_url"]:
                if key in attach and attach[key] is not None:
                    extra_md = attach[key] if key == "text" else f"![Image]({attach[key]})"
                    markdown_parts.append(f"> {extra_md}")

            if 'fields' in attach:
                field_parts = [f"- **{field['title']}** : {field['value']}" for field in attach['fields']]
                markdown_parts.extend([f"> {part}" for part in field_parts])

    if "sections" in data:
        markdown_parts.append("")
        for section in data["sections"]:
            if "activityTitle" in section and section['activityTitle'] not in attachment_titles:
                markdown_parts.append(f"## {section['activityTitle']}")
            if "activitySubtitle" in section:
                markdown_parts.append(section['activitySubtitle'])

    return ['\n'.join(markdown_parts)]


def get_alert_type(data):
    """
    Currently supported are ["grafana-alert", "grafana-resolved", "prometheus-alert", "not-found"]

    :return: alert type
    """

    if ("text" in data) and ("attachments" in data):
        return "slack-webhook"

    # Uptime-kuma has heartbeat
    try:
        if data["heartbeat"]["status"] == 0:
            return "uptime-kuma-alert"
        elif data["heartbeat"]["status"] == 1:
            return "uptime-kuma-resolved"
    except KeyError:
        pass

    # Grafana
    try:
        if data["alerts"][0]["labels"]["grafana_folder"]:
            if data['status'] == "firing":
                return "grafana-alert"
            else:
                return "grafana-resolved"
    except KeyError:
        pass

    # Prometheus
    try:
        if data["alerts"][0]["labels"]["job"]:
            if data['status'] == "firing":
                return "prometheus-alert"
            else:
                return "prometheus-resolved"
    except KeyError:
        pass

    return "not-found"


def get_alert_messages(alert_data: dict, raw_mode=False) -> list:
    """
    Returns a list of messages in markdown format

    :param alert_data: The data send to the bot as dict
    :param raw_mode: Toggles a mode where the data is not parsed but simply returned as code block in a message
    :return: List of alert messages in markdown format
    """

    alert_type = get_alert_type(alert_data)

    if raw_mode:
        return ["**Data received**\n```\n" + str(alert_data).strip("\n").strip() + "\n```"]
    elif alert_type == "not-found":
        return ["**Data received**\n " + dict_to_markdown(alert_data)]
    else:
        try:
            if alert_type == "slack-webhook":
                messages = convert_slack_webhook_to_markdown(alert_data)
            if alert_type == "grafana-alert":
                messages = grafana_alert_to_markdown(alert_data)
            elif alert_type == "grafana-resolved":
                messages = grafana_alert_to_markdown(alert_data)
            elif alert_type == "prometheus-alert":
                messages = prometheus_alert_to_markdown(alert_data)
            elif alert_type == "prometheus-resolved":
                messages = prometheus_alert_to_markdown(alert_data)
            elif alert_type == "uptime-kuma-alert":
                messages = uptime_kuma_alert_to_markdown(alert_data)
            elif alert_type == "uptime-kuma-resolved":
                messages = uptime_kuma_resolved_to_markdown(alert_data)
        except KeyError as e:
            messages = ["**Data received**\n```\n" + str(alert_data).strip(
                "\n").strip() + f"\n```\nThe data was detected as {alert_type} but was not in an expected format. If you want to help the development of this bot, file a bug report [here](https://github.com/moan0s/alertbot/issues)\n{e.with_traceback()}"]
    return messages


def uptime_kuma_alert_to_markdown(alert_data: dict):
    tags_readable = ", ".join([tag["name"] for tag in alert_data["monitor"]["tags"]])
    message = (
        f"""**Firing 🔥**: Monitor down: {alert_data["monitor"]["url"]}

* **Error:** {alert_data["heartbeat"]["msg"]}
* **Started at:** {alert_data["heartbeat"]["time"]}
* **Tags:** {tags_readable}
* **Source:** "Uptime Kuma"
                    """
    )
    return [message]


def dict_to_markdown(alert_data: dict):
    md = ""
    for key_or_dict in alert_data:
        try:
            alert_data[key_or_dict]
        except TypeError:
            md += "  " + dict_to_markdown(key_or_dict)
            continue
        if not (isinstance(alert_data[key_or_dict], str) or isinstance(alert_data[key_or_dict], int)):
            md += "  " + dict_to_markdown(alert_data[key_or_dict])
        else:
            md += f"* {key_or_dict}: {alert_data[key_or_dict]}\n"
    return md


def uptime_kuma_resolved_to_markdown(alert_data: dict):
    tags_readable = ", ".join([tag["name"] for tag in alert_data["monitor"]["tags"]])
    message = (
        f"""**Resolved 💚**: {alert_data["monitor"]["url"]}

* **Status:** {alert_data["heartbeat"]["msg"]}
* **Started at:** {alert_data["heartbeat"]["time"]}
* Duration until resolved {alert_data["heartbeat"]["duration"]}s
* **Tags:** {tags_readable}
* **Source:** "Uptime Kuma"
    """
    )
    return [message]


def grafana_alert_to_markdown(alert_data: dict) -> list:
    """
    Converts a grafana alert json to markdown

    :param alert_data:
    :return: Alerts as formatted markdown string list
    """
    messages = []
    for alert in alert_data["alerts"]:
        name = alert["labels"]["alertname"]
        if "name" in alert["labels"]:
            name = f"{name} - {alert["labels"]["name"]}"
        elif "rulename" in alert["labels"]:
            name = f"{name} - {alert["labels"]["rulename"]}"
        if alert['status'] == "firing":
            message = (
                f"""**Firing 🔥**: {name} ([silence]({alert["silenceURL"]}))"""
            )
        if alert['status'] == "resolved":
            end_at = dateutil.parser.isoparse(alert['endsAt'])
            start_at = dateutil.parser.isoparse(alert['startsAt'])
            message = (
                f"""**Resolved 🥳**: {name}"""
            )
        messages.append(message)
    return messages


def prometheus_alert_to_markdown(alert_data: dict) -> str:
    """
    Converts a prometheus alert json to markdown

    :param alert_data:
    :return: Alert as fomatted markdown
    """
    messages = []
    known_labels = ['alertname', 'instance', 'job']
    for alert in alert_data["alerts"]:
        title = alert['annotations']['description'] if hasattr(alert['annotations'], 'description') else \
            alert['annotations']['summary']
        message = f"""**{alert['status']}** {'💚' if alert['status'] == 'resolved' else '🔥'}: {title}"""
        for label_name in known_labels:
            try:
                message += "\n* **{0}**: {1}".format(label_name.capitalize(), alert["labels"][label_name])
            except:
                pass
        messages.append(message)
    return messages


class AlertBot(Plugin):
    raw_mode = False

    async def send_alert(self, req, room):
        text = await req.text()
        self.log.info(text)
        content = json.loads(f"{text}")
        for message in get_alert_messages(content, self.raw_mode):
            self.log.debug(f"Sending alert to {room}")
            await self.client.send_markdown(room, message)

    @web.post("/webhook/{room_id}")
    async def webhook_room(self, req: Request) -> Response:
        room_id = req.match_info["room_id"].strip()
        try:
            await self.send_alert(req, room=room_id)
        except MForbidden:
            self.log.error(f"Could not send to {room_id}: Forbidden. Most likely the bot is not invited in the room.")
            return json_response('{"status": "forbidden", "error": "forbidden"}', status=403)
        return json_response({"status": "ok"})

    @command.new()
    async def ping(self, evt: MessageEvent) -> None:
        """Answers pong to check if the bot is running"""
        await evt.reply("pong")

    @command.new()
    async def roomid(self, evt: MessageEvent) -> None:
        """Answers with the current room id"""
        await evt.reply(f"`{evt.room_id}`")

    @command.new()
    async def url(self, evt: MessageEvent) -> None:
        """Answers with the url of the webhook"""
        await evt.reply(f"`{self.webapp_url}/webhook/{evt.room_id}`")

    @command.new()
    async def raw(self, evt: MessageEvent) -> None:
        self.raw_mode = not self.raw_mode
        """Switches the bot to raw mode or disables raw mode (mode where data is not formatted but simply forwarded)"""
        await evt.reply(f"Mode is now: `{'raw' if self.raw_mode else 'normal'} mode`")

    @command.new()
    async def help(self, evt: MessageEvent) -> None:
        await self.client.send_markdown(evt.room_id, helpstring)
