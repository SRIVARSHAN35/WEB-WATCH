from django.contrib import admin

from .models import Website, WebsiteChange


@admin.register(Website)
class WebsiteAdmin(admin.ModelAdmin):
	list_display = ("name", "owner", "status", "check_interval", "last_checked")
	list_filter = ("status",)
	search_fields = ("name", "url", "owner__email")
	autocomplete_fields = ("owner",)


@admin.register(WebsiteChange)
class WebsiteChangeAdmin(admin.ModelAdmin):
	list_display = ("website", "detected_at", "previous_hash", "new_hash")
	list_filter = ("detected_at",)
	search_fields = ("website__name", "website__owner__email")
