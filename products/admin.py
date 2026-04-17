from django.contrib import admin
from .models import Category, Product, Enquiry

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'order']
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'is_featured', 'created_at']
    list_filter = ['category', 'is_featured']
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = ['name', 'email', 'product', 'status', 'created_at']
    list_filter = ['status']
