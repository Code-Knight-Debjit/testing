from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
import json, resend
from django.conf import settings
from .models import Category, Product, Enquiry

def product_list(request):
    categories = Category.objects.prefetch_related('products').all()
    active_category = request.GET.get('category', None)
    return render(request, 'products/product_list.html', {
        'categories': categories,
        'active_category': active_category,
    })

@require_POST
def enquire(request):
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
        product = Product.objects.get(id=product_id) if product_id else None
        enquiry = Enquiry.objects.create(
            product=product,
            name=data.get('name', ''),
            email=data.get('email', ''),
            phone=data.get('phone', ''),
            company=data.get('company', ''),
            message=data.get('message', ''),
        )
        # Send email via Resend if configured
        if settings.RESEND_API_KEY:
            try:
                resend.api_key = settings.RESEND_API_KEY
                resend.Emails.send({
                    "from": settings.DEFAULT_FROM_EMAIL,
                    "to": [settings.COMPANY_EMAIL],
                    "subject": f"Product Enquiry: {product.name if product else 'General'}",
                    "html": f"<p>Name: {enquiry.name}</p><p>Email: {enquiry.email}</p><p>Phone: {enquiry.phone}</p><p>Company: {enquiry.company}</p><p>Message: {enquiry.message}</p>",
                })
            except Exception:
                pass
        return JsonResponse({'success': True, 'message': 'Enquiry submitted successfully!'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)
