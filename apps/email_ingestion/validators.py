import re
from rest_framework import serializers


def extract_email(value: str) -> str:
    match = re.search(r"<(.+?)>", value)
    return match.group(1).strip() if match else value.strip()


class InboundEmailValidator(serializers.Serializer):
    sender = serializers.EmailField()
    recipient = serializers.EmailField()
    subject = serializers.CharField(max_length=500, required=False, default="")
    body_plain = serializers.CharField(required=False, default="")
    body_html = serializers.CharField(required=False, default="")
    date = serializers.CharField(required=False, default="")
    message_id = serializers.CharField(required=False, default="")

    def to_internal_value(self, data):
        remapped = data.copy()
        remapped["body_plain"] = data.get("body-plain", "")
        remapped["body_html"] = data.get("body-html", "")
        remapped["date"] = data.get("Date", "")
        remapped["message_id"] = data.get("Message-Id", "")
        remapped["sender"] = extract_email(data.get("sender", ""))
        remapped["recipient"] = extract_email(data.get("recipient", ""))
        return super().to_internal_value(remapped)


class EmlUploadValidator(serializers.Serializer):
    files = serializers.ListField(
        child=serializers.FileField(),
        min_length=1,
        max_length=50,
    )

    def validate_files(self, files):
        for f in files:
            if not f.name.endswith(".eml"):
                raise serializers.ValidationError(
                    f"{f.name} is not a .eml file."
                )
        return files