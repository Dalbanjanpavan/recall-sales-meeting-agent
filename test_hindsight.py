
import os
from dotenv import load_dotenv
from hindsight_client import Hindsight

load_dotenv()

client = Hindsight(
    base_url=os.getenv("HINDSIGHT_BASE_URL"),
    api_key=os.getenv("HINDSIGHT_API_KEY")
)

BANK_ID = "sales-meeting-agent-demo"

try:
    print("Creating memory bank...")

    client.create_bank(
        bank_id=BANK_ID,
        name="Sales Meeting Intelligence"
    )

    print("Storing test memory...")

    client.retain(
        bank_id=BANK_ID,
        content=(
            "Prospect: Acme Technologies. "
            "Contact: Rahul. "
            "Rahul is concerned about pricing and wants "
            "a product demonstration next week."
        )
    )

    print("Retrieving memory...")

    result = client.recall(
        bank_id=BANK_ID,
        query="What concerns does Rahul have and what does he want next?"
    )

    print("\n--- HINDSIGHT MEMORY RESULTS ---")

    for memory in result.results:
        print(memory.text)

    print("\nMemory test completed.")

except Exception as e:
    print("\nERROR:", e)

finally:
    client.close()
