from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.urls import reverse
from django.utils.html import format_html

from catalog.models import PersonFollow, CompanyFollow


User = get_user_model()


class UserAdmin(DjangoUserAdmin):
	# Extend default UserAdmin list display with follow counts
	list_display = tuple(list(DjangoUserAdmin.list_display) + ["person_follow_count_link", "company_follow_count_link"])
	readonly_fields = tuple(list(getattr(DjangoUserAdmin, 'readonly_fields', ())) + ["person_follow_count_link", "company_follow_count_link"])

	def person_follow_count_link(self, obj):
		count = PersonFollow.objects.filter(user=obj).count()
		url = reverse('admin:catalog_personfollow_changelist') + f'?user__id__exact={obj.id}'
		return format_html('<a href="{}">{} person(s)</a>', url, count)
	person_follow_count_link.short_description = 'People following'

	def company_follow_count_link(self, obj):
		count = CompanyFollow.objects.filter(user=obj).count()
		url = reverse('admin:catalog_companyfollow_changelist') + f'?user__id__exact={obj.id}'
		return format_html('<a href="{}">{} company(ies)</a>', url, count)
	company_follow_count_link.short_description = 'Companies following'

	def get_fieldsets(self, request, obj=None):
		fieldsets = list(super().get_fieldsets(request, obj))
		# Add a small readonly section on the change form with follow links
		fieldsets.append(("Following", {"fields": ("person_follow_count_link", "company_follow_count_link")}))
		return fieldsets


# If User is already registered (default auth.UserAdmin), unregister it first
try:
	admin.site.unregister(User)
except Exception:
	# NotRegistered or other issues can be ignored here; we'll proceed to register
	pass

# Register our custom admin
admin.site.register(User, UserAdmin)
