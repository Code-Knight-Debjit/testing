from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify
from datetime import timedelta
import json

from products.models import Category, Product, Enquiry
from contact.models import ContactMessage, ChatMessage

staff_required = user_passes_test(lambda u: u.is_staff, login_url='/dashboard/login/')


def dashboard_login(request):
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('dashboard:home')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user and user.is_staff:
            login(request, user)
            return redirect(request.GET.get('next', '/dashboard/'))
        error = 'Invalid credentials or insufficient permissions.'
    return render(request, 'dashboard/login.html', {'error': error})


def dashboard_logout(request):
    logout(request)
    return redirect('dashboard:login')


@login_required(login_url='/dashboard/login/')
@staff_required
def dashboard_home(request):
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    stats = {
        'total_products':    Product.objects.count(),
        'total_categories':  Category.objects.count(),
        'new_enquiries':     Enquiry.objects.filter(status='new').count(),
        'total_enquiries':   Enquiry.objects.count(),
        'unread_messages':   ContactMessage.objects.filter(is_read=False).count(),
        'total_messages':    ContactMessage.objects.count(),
        'total_chats':       ChatMessage.objects.filter(role='user').count(),
        'chats_this_week':   ChatMessage.objects.filter(role='user', created_at__gte=week_ago).count(),
    }

    recent_enquiries = Enquiry.objects.select_related('product').order_by('-created_at')[:6]
    recent_messages  = ContactMessage.objects.order_by('-created_at')[:6]
    recent_chats     = ChatMessage.objects.filter(role='user').order_by('-created_at')[:6]

    # Chart data: enquiries per day last 7 days
    chart_labels = []
    chart_enquiries = []
    chart_messages = []
    for i in range(6, -1, -1):
        day = now - timedelta(days=i)
        label = day.strftime('%a')
        chart_labels.append(label)
        chart_enquiries.append(
            Enquiry.objects.filter(created_at__date=day.date()).count()
        )
        chart_messages.append(
            ContactMessage.objects.filter(created_at__date=day.date()).count()
        )

    return render(request, 'dashboard/home.html', {
        'stats': stats,
        'recent_enquiries': recent_enquiries,
        'recent_messages': recent_messages,
        'recent_chats': recent_chats,
        'chart_labels': json.dumps(chart_labels),
        'chart_enquiries': json.dumps(chart_enquiries),
        'chart_messages': json.dumps(chart_messages),
    })


# ── PRODUCTS ──────────────────────────────────────────────
@login_required(login_url='/dashboard/login/')
@staff_required
def product_list(request):
    q = request.GET.get('q', '')
    cat_filter = request.GET.get('category', '')
    products = Product.objects.select_related('category').order_by('-created_at')
    if q:
        products = products.filter(Q(name__icontains=q) | Q(description__icontains=q))
    if cat_filter:
        products = products.filter(category__slug=cat_filter)
    categories = Category.objects.all()
    return render(request, 'dashboard/products.html', {
        'products': products,
        'categories': categories,
        'q': q,
        'cat_filter': cat_filter,
    })


@login_required(login_url='/dashboard/login/')
@staff_required
def product_add(request):
    categories = Category.objects.all()
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = slugify(name)
        # ensure unique slug
        base = slug
        i = 1
        while Product.objects.filter(slug=slug).exists():
            slug = f"{base}-{i}"; i += 1

        product = Product(
            name=name,
            slug=slug,
            category_id=request.POST.get('category'),
            description=request.POST.get('description', ''),
            is_featured=request.POST.get('is_featured') == 'on',
        )
        if request.FILES.get('image'):
            product.image = request.FILES['image']
        product.save()

        # Parse specs
        spec_keys   = request.POST.getlist('spec_key')
        spec_values = request.POST.getlist('spec_value')
        specs = {k: v for k, v in zip(spec_keys, spec_values) if k.strip()}
        if specs:
            product.specifications = specs
            product.save()

        return redirect('dashboard:products')
    return render(request, 'dashboard/product_form.html', {
        'categories': categories,
        'product': None,
    })


@login_required(login_url='/dashboard/login/')
@staff_required
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    categories = Category.objects.all()
    if request.method == 'POST':
        product.name = request.POST.get('name', product.name).strip()
        product.category_id = request.POST.get('category', product.category_id)
        product.description = request.POST.get('description', '')
        product.is_featured = request.POST.get('is_featured') == 'on'
        if request.FILES.get('image'):
            product.image = request.FILES['image']

        spec_keys   = request.POST.getlist('spec_key')
        spec_values = request.POST.getlist('spec_value')
        product.specifications = {k: v for k, v in zip(spec_keys, spec_values) if k.strip()}
        product.save()
        return redirect('dashboard:products')
    return render(request, 'dashboard/product_form.html', {
        'categories': categories,
        'product': product,
    })


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def product_delete(request, pk):
    get_object_or_404(Product, pk=pk).delete()
    return JsonResponse({'success': True})


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def product_toggle_featured(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.is_featured = not product.is_featured
    product.save()
    return JsonResponse({'success': True, 'is_featured': product.is_featured})


# ── CATEGORIES ────────────────────────────────────────────
@login_required(login_url='/dashboard/login/')
@staff_required
def category_list(request):
    categories = Category.objects.annotate(product_count=Count('products')).order_by('order', 'name')
    return render(request, 'dashboard/categories.html', {'categories': categories})


@login_required(login_url='/dashboard/login/')
@staff_required
def category_add(request):
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        slug = slugify(name)
        base = slug; i = 1
        while Category.objects.filter(slug=slug).exists():
            slug = f"{base}-{i}"; i += 1
        cat = Category(
            name=name, slug=slug,
            description=request.POST.get('description', ''),
            icon=request.POST.get('icon', ''),
            order=int(request.POST.get('order', 0) or 0),
        )
        if request.FILES.get('image'):
            cat.image = request.FILES['image']
        cat.save()
        return redirect('dashboard:categories')
    return render(request, 'dashboard/category_form.html', {'category': None})


@login_required(login_url='/dashboard/login/')
@staff_required
def category_edit(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        category.name = request.POST.get('name', category.name).strip()
        category.description = request.POST.get('description', '')
        category.icon = request.POST.get('icon', '')
        category.order = int(request.POST.get('order', 0) or 0)
        if request.FILES.get('image'):
            category.image = request.FILES['image']
        category.save()
        return redirect('dashboard:categories')
    return render(request, 'dashboard/category_form.html', {'category': category})


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def category_delete(request, pk):
    get_object_or_404(Category, pk=pk).delete()
    return JsonResponse({'success': True})


# ── ENQUIRIES ─────────────────────────────────────────────
@login_required(login_url='/dashboard/login/')
@staff_required
def enquiry_list(request):
    status_filter = request.GET.get('status', '')
    enquiries = Enquiry.objects.select_related('product__category').order_by('-created_at')
    if status_filter:
        enquiries = enquiries.filter(status=status_filter)
    return render(request, 'dashboard/enquiries.html', {
        'enquiries': enquiries,
        'status_filter': status_filter,
    })


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def enquiry_update_status(request, pk):
    enquiry = get_object_or_404(Enquiry, pk=pk)
    data = json.loads(request.body)
    enquiry.status = data.get('status', enquiry.status)
    enquiry.save()
    return JsonResponse({'success': True})


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def enquiry_delete(request, pk):
    get_object_or_404(Enquiry, pk=pk).delete()
    return JsonResponse({'success': True})


# ── CONTACT MESSAGES ──────────────────────────────────────
@login_required(login_url='/dashboard/login/')
@staff_required
def message_list(request):
    read_filter = request.GET.get('read', '')
    messages = ContactMessage.objects.order_by('-created_at')
    if read_filter == 'unread':
        messages = messages.filter(is_read=False)
    elif read_filter == 'read':
        messages = messages.filter(is_read=True)
    return render(request, 'dashboard/messages.html', {
        'messages': messages,
        'read_filter': read_filter,
    })


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def message_mark_read(request, pk):
    msg = get_object_or_404(ContactMessage, pk=pk)
    msg.is_read = True
    msg.save()
    return JsonResponse({'success': True})


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def message_delete(request, pk):
    get_object_or_404(ContactMessage, pk=pk).delete()
    return JsonResponse({'success': True})


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def message_mark_all_read(request):
    ContactMessage.objects.filter(is_read=False).update(is_read=True)
    return JsonResponse({'success': True})


# ── CHAT MESSAGES ─────────────────────────────────────────
@login_required(login_url='/dashboard/login/')
@staff_required
def chat_list(request):
    sessions = (
        ChatMessage.objects
        .values('session_id')
        .annotate(
            msg_count=Count('id'),
            last_msg=Count('created_at'),
        )
        .order_by('-session_id')
    )
    # get last message per session for display
    from django.db.models import Max
    session_data = (
        ChatMessage.objects
        .values('session_id')
        .annotate(count=Count('id'), latest=Max('created_at'))
        .order_by('-latest')[:50]
    )
    return render(request, 'dashboard/chats.html', {'sessions': session_data})


@login_required(login_url='/dashboard/login/')
@staff_required
def chat_detail(request, session_id):
    messages = ChatMessage.objects.filter(session_id=session_id).order_by('created_at')
    return render(request, 'dashboard/chat_detail.html', {
        'messages': messages,
        'session_id': session_id,
    })


@login_required(login_url='/dashboard/login/')
@staff_required
@require_POST
def chat_delete_session(request, session_id):
    ChatMessage.objects.filter(session_id=session_id).delete()
    return JsonResponse({'success': True})


# ── NOTIFICATIONS API ─────────────────────────────────────
@login_required(login_url='/dashboard/login/')
@staff_required
@require_GET
def notifications_api(request):
    return JsonResponse({
        'new_enquiries':   Enquiry.objects.filter(status='new').count(),
        'unread_messages': ContactMessage.objects.filter(is_read=False).count(),
        'total_unread':    (
            Enquiry.objects.filter(status='new').count() +
            ContactMessage.objects.filter(is_read=False).count()
        ),
    })


# ── RAG MANAGEMENT ────────────────────────────────────────
@login_required(login_url='/dashboard/login/')
@staff_required
def rag_status(request):
    """RAG system status and management page."""
    from rag.retriever import get_index_stats
    from rag.llm_client import check_ollama_health

    index_stats   = get_index_stats()
    ollama_health = check_ollama_health()

    # Check Redis
    redis_ok = False
    try:
        from django.core.cache import cache
        cache.set('rag_ping', '1', 5)
        redis_ok = cache.get('rag_ping') == '1'
    except Exception:
        pass

    llm_models = [
        {'name': 'llama3',    'badge': 'default', 'badge_class': 'badge-ok'},
        {'name': 'mistral',   'badge': 'fast',    'badge_class': 'pill-in-progress'},
        {'name': 'llama3.2',  'badge': None, 'badge_class': ''},
        {'name': 'gemma2',    'badge': 'lightweight', 'badge_class': 'pill-assistant'},
        {'name': 'phi3',      'badge': None, 'badge_class': ''},
        {'name': 'qwen2.5',   'badge': None, 'badge_class': ''},
    ]
    return render(request, 'dashboard/rag_status.html', {
        'index_stats':   index_stats,
        'ollama_health': ollama_health,
        'redis_ok':      redis_ok,
        'llm_models':    llm_models,
    })
