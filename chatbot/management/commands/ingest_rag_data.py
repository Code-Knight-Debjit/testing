"""
chatbot/management/commands/ingest_rag_data.py
Management command to build or update the FAISS vector index
from files in data/knowledge_base/.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Ingest knowledge base documents into the RAG FAISS vector index."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rebuild",
            action="store_true",
            default=False,
            help="Wipe existing index and rebuild from scratch (default: append mode).",
        )
        parser.add_argument(
            "--file",
            type=str,
            default=None,
            help="Path to a single .txt, .pdf, or .json file to ingest.",
        )
        parser.add_argument(
            "--dir",
            type=str,
            default=None,
            help="Path to a directory of files to ingest (all .txt, .pdf, .json).",
        )
        parser.add_argument(
            "--stats",
            action="store_true",
            default=False,
            help="Show current index statistics and exit.",
        )
        parser.add_argument(
            "--also-seed-products",
            action="store_true",
            default=False,
            help="Also ingest product data from the Django database.",
        )

    def handle(self, *args, **options):
        from rag.chunker import file_to_chunks
        from rag.retriever import add_documents, get_index_stats

        if options["stats"]:
            stats = get_index_stats()
            self.stdout.write("\nRAG Index Statistics")
            self.stdout.write("-" * 40)
            if stats["exists"]:
                self.stdout.write(self.style.SUCCESS("  Status:   OK Index exists"))
                self.stdout.write(f"  Vectors:  {stats['total_vectors']}")
                self.stdout.write(f"  Docs:     {stats['total_documents']}")
                self.stdout.write(f"  Sources:  {', '.join(stats['sources'])}")
                self.stdout.write(f"  Path:     {stats['index_path']}")
            else:
                self.stdout.write(self.style.WARNING("  Status:   No index found"))
                self.stdout.write("  Run: python manage.py ingest_rag_data")
            self.stdout.write("")
            return

        rebuild = options["rebuild"]
        all_chunks = []
        all_metadatas = []
        files_processed = 0

        if options["file"]:
            files = [Path(options["file"])]
        elif options["dir"]:
            dir_path = Path(options["dir"])
            if not dir_path.is_dir():
                raise CommandError(f"Directory not found: {dir_path}")
            files = sorted(
                list(dir_path.glob("**/*.txt"))
                + list(dir_path.glob("**/*.pdf"))
                + list(dir_path.glob("**/*.json"))
            )
        else:
            kb_dir = Path(getattr(settings, "RAG_KNOWLEDGE_DIR", "data/knowledge_base"))
            if not kb_dir.exists():
                raise CommandError(
                    f"Knowledge base directory not found: {kb_dir}\n"
                    "Create it and add .json, .txt, or .pdf files."
                )
            files = sorted(
                list(kb_dir.glob("**/*.txt"))
                + list(kb_dir.glob("**/*.pdf"))
                + list(kb_dir.glob("**/*.json"))
            )

        if not files and not options["also_seed_products"]:
            raise CommandError("No files found to ingest. Use --file or --dir to specify input.")

        self.stdout.write(f"\nFound {len(files)} file(s) to process.")
        if rebuild:
            self.stdout.write(
                self.style.WARNING("  WARNING: --rebuild flag set: existing index will be wiped.")
            )

        for filepath in files:
            self.stdout.write(f"  Processing: {filepath.name}", ending="")
            try:
                chunks, metadatas = file_to_chunks(str(filepath))
                if not chunks:
                    self.stdout.write(self.style.WARNING(" -> skipped (0 chunks)"))
                    continue
                all_chunks.extend(chunks)
                all_metadatas.extend(metadatas)
                files_processed += 1
                self.stdout.write(self.style.SUCCESS(f" -> {len(chunks)} chunks"))
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f" -> ERROR ({type(e).__name__}): {e!r}")
                )

        if options["also_seed_products"]:
            self.stdout.write("\n  Ingesting products from database...")
            db_chunks, db_metas = self._get_db_product_chunks()
            all_chunks.extend(db_chunks)
            all_metadatas.extend(db_metas)
            self.stdout.write(self.style.SUCCESS(f"     -> {len(db_chunks)} product chunks"))

        if not all_chunks:
            self.stdout.write(self.style.WARNING("\nNo chunks to index. Nothing saved."))
            return

        self.stdout.write(f"\nGenerating embeddings for {len(all_chunks)} chunks...")
        self.stdout.write("   (This may take a minute on first run - model download required)")

        total = add_documents(all_chunks, all_metadatas, rebuild=rebuild)

        self.stdout.write("\n" + "-" * 50)
        self.stdout.write(self.style.SUCCESS("Ingestion complete!"))
        self.stdout.write(f"   Files processed:  {files_processed}")
        self.stdout.write(f"   Chunks indexed:   {len(all_chunks)}")
        self.stdout.write(f"   Total in index:   {total}")
        self.stdout.write(f"   Mode:             {'REBUILD' if rebuild else 'APPEND'}")
        self.stdout.write("\nNext steps:")
        self.stdout.write("  - Start Ollama:  ollama serve")
        self.stdout.write("  - Start Redis:   redis-server")
        self.stdout.write("  - Start worker:  celery -A anupam_bearings worker -l info")
        self.stdout.write("  - Verify index:  python manage.py ingest_rag_data --stats")
        self.stdout.write("")

    def _get_db_product_chunks(self):
        """Extract product data from Django DB and convert to RAG chunks."""
        from rag.chunker import texts_to_chunks
        from products.models import Category, Product

        texts = []
        metadatas = []

        for p in Product.objects.select_related("category").all():
            spec_text = ""
            if p.specifications:
                spec_text = " | ".join(f"{k}: {v}" for k, v in p.specifications.items())
            full_text = (
                f"Product: {p.name}\n"
                f"Category: {p.category.name}\n"
                f"Description: {p.description}\n"
                + (f"Specifications: {spec_text}" if spec_text else "")
            ).strip()
            texts.append(full_text)
            metadatas.append(
                {
                    "source": "product_database",
                    "title": p.name,
                    "category": p.category.name,
                    "tags": ["product", p.category.slug],
                }
            )

        for c in Category.objects.all():
            text = (
                f"Product Category: {c.name}\n"
                f"Description: {c.description}"
            ).strip()
            texts.append(text)
            metadatas.append(
                {
                    "source": "product_database",
                    "title": c.name,
                    "tags": ["category"],
                }
            )

        if not texts:
            return [], []

        return texts_to_chunks(texts, metadatas)
