-- ==============================================================================
-- RagBait Local Development Seed
-- ==============================================================================
-- Grant vault capability role to local development login role

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragbait_vault_user')
     AND EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'ragbait_vault_service') THEN
    GRANT ragbait_vault_service TO ragbait_vault_user;
  END IF;
END $$;

-- ------------------------------------------------------------------------------
-- Academic Knowledge Base Foundational Seed
-- ------------------------------------------------------------------------------
INSERT INTO public.kb_subjects (name, slug, description, icon_name, display_order, is_active)
VALUES 
  ('Computer Science', 'computer-science', 'Core computing concepts, algorithms, and systems engineering', 'Cpu', 1, true),
  ('Mathematics', 'mathematics', 'Pure and applied mathematics for engineering and computing', 'Brain', 2, true),
  ('Physics', 'physics', 'Fundamental physics, classical mechanics, and electromagnetism', 'Compass', 3, true)
ON CONFLICT (slug) DO UPDATE SET is_active = true;

-- Topics for Computer Science
INSERT INTO public.kb_topics (subject_id, name, slug, description, display_order, is_active)
SELECT s.id, t.name, t.slug, t.description, t.display_order, true
FROM public.kb_subjects s
CROSS JOIN (
  VALUES 
    ('Operating Systems', 'operating-systems', 'Kernel architecture, processes, memory management, and file systems', 1),
    ('Algorithms & Data Structures', 'algorithms-and-data-structures', 'Algorithm analysis, graph theory, sorting, and dynamic programming', 2),
    ('Computer Networks', 'computer-networks', 'TCP/IP protocol stack, routing, sockets, and network security', 3),
    ('Database Systems', 'database-systems', 'Relational database design, SQL querying, indexing, and transactions', 4)
) AS t(name, slug, description, display_order)
WHERE s.slug = 'computer-science'
ON CONFLICT (subject_id, slug) DO UPDATE SET is_active = true;

-- Topics for Mathematics
INSERT INTO public.kb_topics (subject_id, name, slug, description, display_order, is_active)
SELECT s.id, t.name, t.slug, t.description, t.display_order, true
FROM public.kb_subjects s
CROSS JOIN (
  VALUES 
    ('Calculus', 'calculus', 'Limits, derivatives, integrals, and multivariable functions', 1),
    ('Linear Algebra', 'linear-algebra', 'Vectors, matrices, linear transformations, and eigenvalues', 2),
    ('Probability & Statistics', 'probability-and-statistics', 'Probability distributions, estimation, and hypothesis testing', 3)
) AS t(name, slug, description, display_order)
WHERE s.slug = 'mathematics'
ON CONFLICT (subject_id, slug) DO UPDATE SET is_active = true;

-- Topics for Physics
INSERT INTO public.kb_topics (subject_id, name, slug, description, display_order, is_active)
SELECT s.id, t.name, t.slug, t.description, t.display_order, true
FROM public.kb_subjects s
CROSS JOIN (
  VALUES 
    ('Classical Mechanics', 'classical-mechanics', 'Newtonian dynamics, kinematics, and energy conservation', 1),
    ('Electromagnetism', 'electromagnetism', 'Electrostatics, Gauss law, magnetic fields, and circuits', 2),
    ('Quantum Physics', 'quantum-physics', 'Wave-particle duality, photons, and quantum states', 3)
) AS t(name, slug, description, display_order)
WHERE s.slug = 'physics'
ON CONFLICT (subject_id, slug) DO UPDATE SET is_active = true;

