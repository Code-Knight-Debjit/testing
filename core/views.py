from django.shortcuts import render
from products.models import Category, Product

def home(request):
    categories = Category.objects.all()[:5]
    featured_products = Product.objects.filter(is_featured=True)[:8]
    return render(request, 'core/home.html', {
        'categories': categories,
        'featured_products': featured_products,
    })

def about(request):
    return render(request, 'core/about.html')

def gallery(request):
    return render(request, 'core/gallery.html')
