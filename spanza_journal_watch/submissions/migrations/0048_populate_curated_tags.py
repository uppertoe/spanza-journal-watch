from django.db import migrations

# ── Curated tag definitions ──
# (slug, display_order) — the slug doubles as the text field
CURATED_TAGS = [
    ("airway", 1),
    ("allergy-anaphylaxis", 2),
    ("cardiac", 3),
    ("climate-sustainability", 4),
    ("consent-ethics", 5),
    ("covid-pandemic", 6),
    ("day-surgery", 7),
    ("delirium-emergence", 8),
    ("difficult-airway", 9),
    ("education-training", 10),
    ("eeg-neuromonitoring", 11),
    ("equipment", 12),
    ("fasting", 13),
    ("fluid-therapy", 14),
    ("general-anaesthesia", 15),
    ("guideline", 16),
    ("icu-critical-care", 17),
    ("leadership-wellbeing", 18),
    ("local-anaesthetics", 19),
    ("monitoring", 20),
    ("neonatal", 21),
    ("neurodevelopment", 22),
    ("neuromuscular", 23),
    ("neurosurgery", 24),
    ("non-opioid-analgesics", 25),
    ("obesity", 26),
    ("obstetric-anaesthesia", 27),
    ("opioids", 28),
    ("orthopaedics", 29),
    ("pain", 30),
    ("perioperative", 31),
    ("pharmacology", 32),
    ("point-of-care-ultrasound", 33),
    ("postoperative-complications", 34),
    ("preoperative-assessment", 35),
    ("quality-improvement", 36),
    ("regional-anaesthesia", 37),
    ("respiratory", 38),
    ("resuscitation", 39),
    ("safety", 40),
    ("scoliosis-spine", 41),
    ("sedation", 42),
    ("simulation", 43),
    ("transfusion", 44),
    ("trauma", 45),
    ("vascular-access", 46),
    ("volatile-anaesthetics", 47),
]

# ── Old tag → curated tag mapping ──
# Keys: existing tag text (slug). Values: curated tag slug.
# Tags not listed here will be deactivated.
OLD_TO_CURATED = {
    "airway": "airway",
    "intubation": "airway",
    "laryngoscopy": "airway",
    "laryngospasm": "airway",
    "lma": "airway",
    "videolaryngoscopy": "airway",
    "thrive": "airway",
    "rsi": "airway",
    "anaphylaxis": "allergy-anaphylaxis",
    "cardiac": "cardiac",
    "climate": "climate-sustainability",
    "sustainability": "climate-sustainability",
    "covid": "covid-pandemic",
    "pandemic": "covid-pandemic",
    "delirium": "delirium-emergence",
    "emergence": "delirium-emergence",
    "education": "education-training",
    "fellowship": "education-training",
    "simulation": "simulation",
    "eeg": "eeg-neuromonitoring",
    "bis": "eeg-neuromonitoring",
    "nirs": "eeg-neuromonitoring",
    "equipment": "equipment",
    "fasting": "fasting",
    "fluid": "fluid-therapy",
    "fluids": "fluid-therapy",
    "crystalloid": "fluid-therapy",
    "guideline": "guideline",
    "picu": "icu-critical-care",
    "leadership": "leadership-wellbeing",
    "wellbeing": "leadership-wellbeing",
    "empathy": "leadership-wellbeing",
    "monitoring": "monitoring",
    "neonate": "neonatal",
    "premature": "neonatal",
    "infant": "neonatal",
    "neurodevelopment": "neurodevelopment",
    "neuroprotection": "neurodevelopment",
    "neurophysiology": "neurodevelopment",
    "neurosurgery": "neurosurgery",
    "opioid": "opioids",
    "methadone": "opioids",
    "orthopaedics": "orthopaedics",
    "scoliosis": "scoliosis-spine",
    "spine": "scoliosis-spine",
    "analgesia": "pain",
    "pain": "pain",
    "perioperative": "perioperative",
    "pharmacology": "pharmacology",
    "dexmedetomidine": "pharmacology",
    "clonidine": "pharmacology",
    "remimazolam": "pharmacology",
    "ultrasound": "point-of-care-ultrasound",
    "complication": "postoperative-complications",
    "ponv": "postoperative-complications",
    "quality": "quality-improvement",
    "documentation": "quality-improvement",
    "emr": "quality-improvement",
    "regional": "regional-anaesthesia",
    "block": "regional-anaesthesia",
    "caudal": "regional-anaesthesia",
    "nerve": "regional-anaesthesia",
    "intrathecal": "regional-anaesthesia",
    "spinal": "regional-anaesthesia",
    "respiratory": "respiratory",
    "hfnp": "respiratory",
    "ventilation": "respiratory",
    "asthma": "respiratory",
    "resuscitation": "resuscitation",
    "safety": "safety",
    "sedation": "sedation",
    "premed": "sedation",
    "premedication": "sedation",
    "transfusion": "transfusion",
    "blood": "transfusion",
    "heparin": "transfusion",
    "anticoagulation": "transfusion",
    "volatile": "volatile-anaesthetics",
    "sevoflurane": "volatile-anaesthetics",
    "n2o": "volatile-anaesthetics",
    "nitrous": "volatile-anaesthetics",
    "tiva": "general-anaesthesia",
    "induction": "general-anaesthesia",
    "lines": "vascular-access",
    "arterial": "vascular-access",
    "anxiety": "preoperative-assessment",
    "screening": "preoperative-assessment",
    "mri": "monitoring",
    "endoscopy": "day-surgery",
    "ent": "day-surgery",
    "gender": "consent-ethics",
    "inequity": "consent-ethics",
    "aboriginal": "consent-ethics",
    "first-nations": "consent-ethics",
    "maori": "consent-ethics",
    "disability": "consent-ethics",
    "public-health": "consent-ethics",
    "global": "consent-ethics",
    "ai": "quality-improvement",
    "ml": "quality-improvement",
    "olv": "respiratory",
    "apls": "resuscitation",
    "nap": "safety",
    "tofoa": "safety",
    "ttm": "resuscitation",
    "survey": "quality-improvement",
    "rct": "guideline",
    "bayesian": "guideline",
    "review": "guideline",
    "preclinical": "pharmacology",
    "editorial": "guideline",
    "behaviour": "preoperative-assessment",
    "caustic": "safety",
    "battery": "safety",
    "technique": "regional-anaesthesia",
    "neuromuscular": "neuromuscular",
}

# ── MeSH term → curated tag mapping ──
MESH_TO_CURATED = {
    # Airway
    "Airway Management": "airway",
    "Intubation, Intratracheal": "airway",
    "Laryngoscopy": "airway",
    "Laryngoscopes": "airway",
    "Laryngeal Masks": "airway",
    "Airway Extubation": "airway",
    "Apnea": "airway",
    # Difficult airway
    "Airway Obstruction": "difficult-airway",
    # Allergy
    "Drug Hypersensitivity": "allergy-anaphylaxis",
    "Anaphylaxis": "allergy-anaphylaxis",
    "Malignant Hyperthermia": "allergy-anaphylaxis",
    # Cardiac
    "Cardiac Surgical Procedures": "cardiac",
    "Cardiopulmonary Bypass": "cardiac",
    "Heart Defects, Congenital": "cardiac",
    "Heart Arrest": "cardiac",
    "Cardiovascular Diseases": "cardiac",
    # Climate
    "Carbon Footprint": "climate-sustainability",
    # COVID
    "COVID-19": "covid-pandemic",
    "SARS-CoV-2": "covid-pandemic",
    "Pandemics": "covid-pandemic",
    # Day surgery
    "Ambulatory Surgical Procedures": "day-surgery",
    "Tonsillectomy": "day-surgery",
    "Adenoidectomy": "day-surgery",
    # Delirium
    "Emergence Delirium": "delirium-emergence",
    "Delirium": "delirium-emergence",
    "Postoperative Cognitive Complications": "delirium-emergence",
    "Neurocognitive Disorders": "delirium-emergence",
    # Education
    "Clinical Competence": "education-training",
    "Simulation Training": "education-training",
    "Fellowships and Scholarships": "education-training",
    "Internship and Residency": "education-training",
    "Faculty, Medical": "education-training",
    # EEG / Neuromonitoring
    "Electroencephalography": "eeg-neuromonitoring",
    "Consciousness Monitors": "eeg-neuromonitoring",
    "Consciousness": "eeg-neuromonitoring",
    # Equipment
    "Equipment Design": "equipment",
    # Fasting
    "Fasting": "fasting",
    "Gastric Emptying": "fasting",
    "Gastrointestinal Contents": "fasting",
    # Fluid therapy
    "Fluid Therapy": "fluid-therapy",
    # General anaesthesia
    "Anesthesia, General": "general-anaesthesia",
    "Anesthetics, Intravenous": "general-anaesthesia",
    "Propofol": "general-anaesthesia",
    "Anesthesia, Intravenous": "general-anaesthesia",
    "Anesthesia Recovery Period": "general-anaesthesia",
    # Guideline
    "Practice Guidelines as Topic": "guideline",
    "Delphi Technique": "guideline",
    "Consensus": "guideline",
    # ICU / Critical care
    "Critical Care": "icu-critical-care",
    "Intensive Care Units": "icu-critical-care",
    "Critical Illness": "icu-critical-care",
    "Respiration, Artificial": "icu-critical-care",
    # Leadership
    "Leadership": "leadership-wellbeing",
    "Societies, Medical": "leadership-wellbeing",
    # Local anaesthetics
    "Anesthetics, Local": "local-anaesthetics",
    "Bupivacaine": "local-anaesthetics",
    "Ropivacaine": "local-anaesthetics",
    "Lidocaine": "local-anaesthetics",
    # Monitoring
    "Monitoring, Intraoperative": "monitoring",
    "Monitoring, Physiologic": "monitoring",
    "Blood Pressure": "monitoring",
    "Hemodynamics": "monitoring",
    "Oxygen Saturation": "monitoring",
    "Neuromuscular Monitoring": "monitoring",
    # Neonatal
    "Infant, Newborn": "neonatal",
    "Premature Birth": "neonatal",
    "Infant, Premature": "neonatal",
    # Neurodevelopment
    "Brain": "neurodevelopment",
    "Cognition": "neurodevelopment",
    "Neurons": "neurodevelopment",
    # Neuromuscular
    "Neuromuscular Blockade": "neuromuscular",
    "Neuromuscular Blocking Agents": "neuromuscular",
    "Neuromuscular Nondepolarizing Agents": "neuromuscular",
    "Sugammadex": "neuromuscular",
    "Rocuronium": "neuromuscular",
    "Neostigmine": "neuromuscular",
    # Obstetric
    "Anesthesia, Obstetrical": "obstetric-anaesthesia",
    "Analgesia, Obstetrical": "obstetric-anaesthesia",
    "Cesarean Section": "obstetric-anaesthesia",
    "Pregnancy": "obstetric-anaesthesia",
    # Opioids
    "Analgesics, Opioid": "opioids",
    "Morphine": "opioids",
    "Fentanyl": "opioids",
    "Remifentanil": "opioids",
    "Opioid-Related Disorders": "opioids",
    # Non-opioid analgesics
    "Dexamethasone": "non-opioid-analgesics",
    "Ketamine": "non-opioid-analgesics",
    "Dexmedetomidine": "non-opioid-analgesics",
    "Tranexamic Acid": "non-opioid-analgesics",
    # Orthopaedics
    "Arthroplasty, Replacement, Knee": "orthopaedics",
    "Arthroplasty, Replacement, Hip": "orthopaedics",
    "Hip Fractures": "orthopaedics",
    "Orthopedic Procedures": "orthopaedics",
    # Pain
    "Postoperative Pain": "pain",
    "Pain Measurement": "pain",
    "Pain Management": "pain",
    "Chronic Pain": "pain",
    "Acute Pain": "pain",
    "Neuralgia": "pain",
    "Low Back Pain": "pain",
    "Hyperalgesia": "pain",
    "Pain": "pain",
    "Nociception": "pain",
    # Perioperative
    "Perioperative Care": "perioperative",
    "Preoperative Care": "perioperative",
    "Postoperative Care": "perioperative",
    "Intraoperative Care": "perioperative",
    "Enhanced Recovery After Surgery": "perioperative",
    "Perioperative Period": "perioperative",
    "Elective Surgical Procedures": "perioperative",
    "Length of Stay": "perioperative",
    # Pharmacology
    "Dose-Response Relationship, Drug": "pharmacology",
    "Hypnotics and Sedatives": "pharmacology",
    "Benzodiazepines": "pharmacology",
    "Glucagon-Like Peptide-1 Receptor Agonists": "pharmacology",
    # Point-of-care ultrasound
    "Ultrasonography, Interventional": "point-of-care-ultrasound",
    "Ultrasonography": "point-of-care-ultrasound",
    "Point-of-Care Systems": "point-of-care-ultrasound",
    # Postoperative complications
    "Postoperative Complications": "postoperative-complications",
    "Intraoperative Complications": "postoperative-complications",
    "Postoperative Nausea and Vomiting": "postoperative-complications",
    "Acute Kidney Injury": "postoperative-complications",
    "Hypotension": "postoperative-complications",
    # Preoperative assessment
    "Anxiety": "preoperative-assessment",
    "Obesity": "preoperative-assessment",
    "Frailty": "preoperative-assessment",
    "Frail Elderly": "preoperative-assessment",
    "Multimorbidity": "preoperative-assessment",
    # Quality improvement
    "Quality Improvement": "quality-improvement",
    "Patient Safety": "quality-improvement",
    "Medical Errors": "quality-improvement",
    "Electronic Health Records": "quality-improvement",
    "Artificial Intelligence": "quality-improvement",
    "Machine Learning": "quality-improvement",
    # Regional anaesthesia
    "Nerve Block": "regional-anaesthesia",
    "Anesthesia, Conduction": "regional-anaesthesia",
    "Brachial Plexus Block": "regional-anaesthesia",
    "Brachial Plexus": "regional-anaesthesia",
    "Peripheral Nerves": "regional-anaesthesia",
    "Abdominal Muscles": "regional-anaesthesia",
    "Paraspinal Muscles": "regional-anaesthesia",
    "Intercostal Nerves": "regional-anaesthesia",
    "Femoral Nerve": "regional-anaesthesia",
    "Analgesia, Epidural": "regional-anaesthesia",
    "Anesthesia, Epidural": "regional-anaesthesia",
    "Anesthesia, Spinal": "regional-anaesthesia",
    "Spinal Cord Stimulation": "regional-anaesthesia",
    "Regional Anesthesia": "regional-anaesthesia",
    "Anesthesia, Local": "regional-anaesthesia",
    # Respiratory
    "Oxygen Inhalation Therapy": "respiratory",
    "Positive-Pressure Respiration": "respiratory",
    "Bronchoscopy": "respiratory",
    "Lung Diseases": "respiratory",
    "Respiratory Insufficiency": "respiratory",
    "Tidal Volume": "respiratory",
    "Hypoxia": "respiratory",
    # Resuscitation
    "Cardiopulmonary Resuscitation": "resuscitation",
    # Safety
    "Patient Safety": "safety",
    # Scoliosis / spine
    "Scoliosis": "scoliosis-spine",
    # Sedation
    "Procedural Sedation": "sedation",
    "Conscious Sedation": "sedation",
    # Simulation
    "Simulation Training": "simulation",
    "Video Recording": "simulation",
    # Transfusion
    "Blood Transfusion": "transfusion",
    "Erythrocyte Transfusion": "transfusion",
    "Blood Loss, Surgical": "transfusion",
    "Thrombelastography": "transfusion",
    "Anticoagulants": "transfusion",
    "Heparin": "transfusion",
    # Vascular access
    "Catheterization, Central Venous": "vascular-access",
    "Catheterization, Peripheral": "vascular-access",
    # Volatile anaesthetics
    "Anesthetics, Inhalation": "volatile-anaesthetics",
    "Sevoflurane": "volatile-anaesthetics",
    "Isoflurane": "volatile-anaesthetics",
    "Desflurane": "volatile-anaesthetics",
    "Nitrous Oxide": "volatile-anaesthetics",
    "Methyl Ethers": "volatile-anaesthetics",
    "Anesthesia, Inhalation": "volatile-anaesthetics",
    # Obesity (separate curated tag)
    "Obesity": "obesity",
    # Neurosurgery
    "Neurosurgical Procedures": "neurosurgery",
}


def populate_curated_tags(apps, schema_editor):
    Tag = apps.get_model("submissions", "Tag")
    MeshTagMapping = apps.get_model("submissions", "MeshTagMapping")

    # 1. Create curated tags
    curated_tag_objects = {}
    for slug, order in CURATED_TAGS:
        tag, _created = Tag.objects.get_or_create(
            text=slug,
            defaults={"slug": slug, "active": True, "curated": True, "display_order": order},
        )
        if not tag.curated:
            tag.curated = True
            tag.display_order = order
            tag.save()
        curated_tag_objects[slug] = tag

    # 2. Map old tags → curated tags (reassign articles)
    for old_tag in Tag.objects.filter(curated=False):
        curated_slug = OLD_TO_CURATED.get(old_tag.text)
        if curated_slug and curated_slug in curated_tag_objects:
            curated_tag = curated_tag_objects[curated_slug]
            # Move all articles from old tag to curated tag
            for article in old_tag.articles.all():
                curated_tag.articles.add(article)
            # Deactivate old tag
            old_tag.active = False
            old_tag.save()
        else:
            # No mapping — deactivate
            old_tag.active = False
            old_tag.save()

    # 3. Populate MeSH → tag mappings
    for mesh_term, curated_slug in MESH_TO_CURATED.items():
        if curated_slug in curated_tag_objects:
            MeshTagMapping.objects.get_or_create(
                mesh_term=mesh_term,
                defaults={"tag": curated_tag_objects[curated_slug]},
            )

    # 4. Auto-tag all articles from MeSH data
    PubmedArticle = apps.get_model("backend", "PubmedArticle")
    mappings_cache = {m.mesh_term: m.tag for m in MeshTagMapping.objects.select_related("tag").all()}

    tagged_count = 0
    for article in PubmedArticle.objects.exclude(metadata_json={}).iterator():
        mesh_terms = (article.metadata_json or {}).get("mesh_terms", [])
        if not mesh_terms:
            continue
        tags_to_add = set()
        for term in mesh_terms:
            tag = mappings_cache.get(term)
            if tag:
                tags_to_add.add(tag.pk)
        if tags_to_add:
            article.tags.add(*tags_to_add)
            tagged_count += 1


def reverse_populate(apps, schema_editor):
    Tag = apps.get_model("submissions", "Tag")
    MeshTagMapping = apps.get_model("submissions", "MeshTagMapping")
    # Remove all MeSH mappings
    MeshTagMapping.objects.all().delete()
    # Re-activate all tags, unset curated
    Tag.objects.update(curated=False, active=True, display_order=0)


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0047_tag_curated_mesh_tag_mapping"),
        ("backend", "0045_watchedjournal_add_medline_ta_nlm_id_display_name"),
    ]

    operations = [
        migrations.RunPython(populate_curated_tags, reverse_populate),
    ]
