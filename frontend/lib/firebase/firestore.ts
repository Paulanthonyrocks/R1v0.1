import { collection, onSnapshot, addDoc, QuerySnapshot, DocumentData, FirestoreError, DocumentReference, QueryDocumentSnapshot, withConverter, Unsubscribe } from 'firebase/firestore';
import { db } from '@/lib/firebase'; // Import db from your firebase configuration file

export interface TrafficData {
  id: string;
  flow: number;
  averageSpeed: number;
  incidentCount: number;
}

export interface IncidentData {
    area: string;
    description: string;
  }

export const subscribeToTrafficData = (
  callback: (data: TrafficData[] | null) => void
) => {
  if (!db) {
    // Return an empty function if db is null
    return () => {};
  }

  const unsubscribe: Unsubscribe = onSnapshot(collection(db, 'trafficData'),
    (querySnapshot: QuerySnapshot<DocumentData>) => {
        const data: TrafficData[] = [];
      querySnapshot.forEach((doc: QueryDocumentSnapshot<DocumentData>) => {        
        const docData = doc.data();
        data.push({
          id: doc.id,
          flow: docData.flow,
          averageSpeed: docData.averageSpeed,
          incidentCount: docData.incidentCount,
        });
      });
      callback(data);
    },
    (error: FirestoreError) => {
      console.error('Error subscribing to traffic data:', error);
      callback(null);
    }
  );
  return unsubscribe;
};

export const createIncident = async (incidentData: IncidentData) => {
  try {
    const incidentsCollection = collection(db, 'incidents').withConverter<IncidentData>({
      toFirestore: (incident: IncidentData) => {
        return {
          area: incident.area,
          description: incident.description,
        };
      },
      fromFirestore: (snapshot) => {
        const data = snapshot.data();
        return {
          area: data.area,
          description: data.description,
        } as IncidentData;
      },
    });
    const docRef: DocumentReference<IncidentData> = await addDoc(
      incidentsCollection,
      incidentData
    );
    console.log("Document written with ID: ", docRef.id);
  } catch (e) {
    console.error("Error adding document: ", e);
    throw e;
  }
};
