from datetime import datetime, timedelta
import config as c


def register_filters(app):
    @app.template_filter('to_datetime')
    def to_datetime(iso_str):
        if not iso_str:
            return None
        try:
            return datetime.fromisoformat(iso_str.replace('Z', '+00:00')).astimezone(c.JST)
        except (ValueError, TypeError):
            return None

    @app.template_filter('seconds')
    def seconds_filter(val):
        try:
            return timedelta(seconds=int(val))
        except (ValueError, TypeError):
            return timedelta(seconds=0)

    @app.template_filter('duration_format')
    def duration_format(val):
        if not val:
            return "0m"
        if isinstance(val, timedelta):
            seconds = int(val.total_seconds())
        else:
            seconds = int(val)
        if seconds < 0:
            return "まもなく終了"
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m" if h > 0 else f"{m}m"

    @app.template_filter('timestamp_to_date')
    def timestamp_to_date(ts):
        if not ts:
            return "-"
        return datetime.fromtimestamp(ts, c.JST).strftime('%Y-%m-%d %H:%M')

    @app.template_filter('format_date')
    def format_date(iso_str):
        if not iso_str:
            return "-"
        try:
            dt = datetime.fromisoformat(iso_str.replace('Z', '+00:00')).astimezone(c.JST)
            return dt.strftime('%Y/%m/%d %H:%M')
        except (ValueError, TypeError):
            return iso_str
