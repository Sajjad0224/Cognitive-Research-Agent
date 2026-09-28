import re
from django.conf import settings
from apps.investigations.services import accessible_evidence, EventLogger

def classify_intent(text):
    t=text.lower().strip()
    if t.startswith(('i think','i believe','my hypothesis','hypothesis:')): return 'hypothesis'
    if any(x in t for x in ['interview','question the ','ask ']): return 'interview'
    if any(x in t for x in ['show me','find evidence','give me evidence','retrieve','open evidence']): return 'evidence_request'
    if any(x in t for x in ['why','how','what','when','where','who','which','is there','are there']): return 'question'
    return 'analysis'

def deterministic_answer(session,message):
    intent=classify_intent(message); ev=list(accessible_evidence(session)); text=message.lower()
    if intent=='evidence_request':
        hits=[e for e in ev if any(term in (e.title+' '+e.content).lower() for term in re.findall(r'[a-z]{4,}',text))]
        items=hits[:8] or ev[:8]
        return 'Available evidence:\n'+'\n'.join(f'- {e.title}: {e.content[:220]}' for e in items)
    terms=set(re.findall(r'[a-z]{5,}',text))
    scored=sorted(((sum(t in (e.title+' '+e.content).lower() for t in terms),e) for e in ev),reverse=True,key=lambda x:x[0])
    relevant=[e for score,e in scored[:5] if score>0]
    if relevant:
        return 'Relevant case evidence I can provide without revealing the solution:\n'+'\n'.join(f'- {e.title}: {e.content[:260]}' for e in relevant)
    return 'I cannot determine that from the currently accessible evidence. Consider identifying the missing fact, testing an alternative hypothesis, or requesting a specific evidence category.'

def answer(session,message):
    intent=classify_intent(message)
    EventLogger.log(session,'question_asked' if intent=='question' else 'agent_interaction',{'message':message,'intent':intent})
    if getattr(settings,'OPENAI_API_KEY',''):
        try:
            from openai import OpenAI
            client=OpenAI(api_key=settings.OPENAI_API_KEY)
            ev=list(accessible_evidence(session))
            context='\n'.join(f'{e.title}: {e.content}' for e in ev)
            system=("You are the controlled case agent. Never reveal hidden truth, author-only solution hypotheses, "
                     "reliability numbers, or author-only metadata. Answer only from accessible case evidence. "
                     "If evidence is insufficient, say so. Encourage investigation rather than solving for the user.\n"
                     f"CASE: {session.case.title}\nOBJECTIVE: {session.case.objective}\nACCESSIBLE EVIDENCE:\n{context}")
            r=client.chat.completions.create(model=settings.OPENAI_MODEL,messages=[{'role':'system','content':system},{'role':'user','content':message}],temperature=.2,max_tokens=500)
            return r.choices[0].message.content
        except Exception:
            pass
    return deterministic_answer(session,message)
