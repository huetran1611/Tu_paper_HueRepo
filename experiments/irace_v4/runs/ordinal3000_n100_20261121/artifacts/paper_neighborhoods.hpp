// AMSTS neighbourhoods, paper Section 5.3.2. Included by the v3 translation unit.
// A trip contains customers only; the enclosing vector preserves trip order.
using Trips = vector<vi>;
using FleetTrips = vector<Trips>;

struct N7Insertion {
    double delta;
    bool new_trip;
    int trip,position;
};

// Three positions across the ENTIRE destination vehicle, including new trips.
// Distance is only a ranking proxy. Every shortlisted complete chain still
// uses the unchanged time-dependent evaluator, penalties, tabu and aspiration.
static vector<N7Insertion> n7_top3_insertions(const Trips& trips,int customer,bool truck) {
    const auto& distances=truck?distance_matrix:drone_distance_matrix;
    vector<N7Insertion> choices;
    for(int t=0;t<(int)trips.size();++t) {
        const auto& trip=trips[t];if(trip.empty()) continue;
        for(int p=0;p<=(int)trip.size();++p) {
            int a=p?trip[p-1]:0,b=p<(int)trip.size()?trip[p]:0;
            choices.push_back({distances[a][customer]+distances[customer][b]-distances[a][b],false,t,p});
        }
    }
    for(int t=0;t<=(int)trips.size();++t) {
        if(t>0&&trips[t-1].empty()) continue;
        choices.push_back({distances[0][customer]+distances[customer][0],true,t,0});
    }
    auto less=[](const N7Insertion& a,const N7Insertion& b) {
        return tie(a.delta,a.new_trip,a.trip,a.position)<tie(b.delta,b.new_trip,b.trip,b.position);
    };
    int keep=min(3,(int)choices.size());
    partial_sort(choices.begin(),choices.begin()+keep,choices.end(),less);
    choices.resize(keep);return choices;
}

static Trips split_trips(const vi& route) {
    Trips trips;
    vi trip;
    for (int node : route) {
        if (node) trip.push_back(node);
        else if (!trip.empty()) { trips.push_back(trip); trip.clear(); }
    }
    if (!trip.empty()) trips.push_back(trip);
    return trips;
}

static vi join_trips(const Trips& trips) {
    vi route{0};
    for (const vi& trip : trips) if (!trip.empty()) {
        route.insert(route.end(), trip.begin(), trip.end());
        route.push_back(0);
    }
    return route;
}

// Enumerate every within-trip position AND a new trip at every trip boundary.
// Empty trips are retained until materialization, so indices stay stable in a move.
template<class Visitor>
static void visit_insertions(const Trips& trips, const vi& block, Visitor visit) {
    for (int r = 0; r < (int)trips.size(); ++r) {
        if (trips[r].empty()) continue;
        for (int p = 0; p <= (int)trips[r].size(); ++p) {
            Trips changed = trips;
            changed[r].insert(changed[r].begin() + p, block.begin(), block.end());
            visit(changed);
        }
    }
    for (int r = 0; r <= (int)trips.size(); ++r) {
        Trips changed = trips;
        changed.insert(changed.begin() + r, block);
        visit(changed);
    }
}

// Section 5.3.4: relocated customers, exchanged blocks, and modified trip edges.
// Entity keys are independent of mutable trip indices. N0/N7 use customers;
// N2 uses its ordered pair; N1/N5/N6 use both exchanged singleton/pair blocks.
// All operators also forbid restoring removed directed edges. This catches
// inverse moves with an alternative description (e.g. adjacent relocations).
using MoveEdge = tuple<int, int, int>; // physical vehicle, from, to
enum class TabuEntityKind { Customer, OrderedPair };
using MoveEntity = tuple<TabuEntityKind, int, int>;
static vector<map<MoveEdge, int>> paper_tabu(NUM_NEIGHBORHOODS);
static vector<map<MoveEntity, int>> paper_entity_tabu(NUM_NEIGHBORHOODS);
static vector<MoveEdge> pending_removed_edges;
static vector<MoveEntity> pending_move_entities;
static int pending_operator = -1;
static bool paper_candidate_found = false;

static MoveEntity tabu_block_attribute(const vi& block) {
    if(block.size()==1) return {TabuEntityKind::Customer,block[0],0};
    if(block.size()==2) return {TabuEntityKind::OrderedPair,block[0],block[1]};
    throw logic_error("Tabu entity must be a singleton or an ordered pair");
}

static vector<MoveEdge> vehicle_edges(int vehicle, const vi& route) {
    vector<MoveEdge> edges;
    for (int k = 1; k < (int)route.size(); ++k)
        if (route[k-1] != route[k]) edges.emplace_back(vehicle, route[k-1], route[k]);
    sort(edges.begin(), edges.end());
    return edges;
}

static void clear_pending_tabu_attributes() {
    pending_removed_edges.clear();
    pending_move_entities.clear();
    pending_operator = -1;
}

static void commit_pending_tabu_attributes(int iter) {
    if (pending_operator >= 0) {
        const int expiry = tabu_expiration_iteration(iter, TABU_TENURE_BASE);
        for (const auto& edge : pending_removed_edges)
            paper_tabu[pending_operator][edge] = expiry;
        for (const auto& entity : pending_move_entities)
            paper_entity_tabu[pending_operator][entity] = expiry;
    }
    clear_pending_tabu_attributes();
}

static void clear_paper_tabu() {
    for (auto& memory : paper_tabu) memory.clear();
    for (auto& memory : paper_entity_tabu) memory.clear();
    clear_pending_tabu_attributes();
}

// Mutate/undo only the destination vehicle. Empty placeholders retain stable
// trip indices during a move; equivalent gaps around them are visited once.
template<class Visitor>
static void visit_insertions_inplace(Trips& trips,const vi& block,Visitor visit) {
    for(int r=0;r<(int)trips.size();++r) {
        if(trips[r].empty()) continue;
        for(int p=0;p<=(int)trips[r].size();++p) {
            trips[r].insert(trips[r].begin()+p,block.begin(),block.end());
            visit(false,r,p);
            trips[r].erase(trips[r].begin()+p,trips[r].begin()+p+block.size());
        }
    }
    vi inserted=block;
    for(int r=0;r<=(int)trips.size();++r) {
        if(r>0 && trips[r-1].empty()) continue;
        trips.insert(trips.begin()+r,std::move(inserted));
        visit(true,r,0);
        inserted=std::move(trips[r]);
        trips.erase(trips.begin()+r);
    }
}

static void flatten_trips_into(const Trips& trips,vi& route) {
    route.clear();route.push_back(0);
    for(const auto& trip:trips) if(!trip.empty()) {
        route.insert(route.end(),trip.begin(),trip.end());route.push_back(0);
    }
}

static Solution paper_local_search(const Solution& initial,int op,int iter,
                                  double reference,double (*cost)(const Solution&),
                                  bool fleet_scope) {
    check_search_time_limit();clear_pending_tabu_attributes();paper_candidate_found=false;
    for(auto it=paper_tabu[op].begin();it!=paper_tabu[op].end();) {
        if(it->second<=iter) it=paper_tabu[op].erase(it);else ++it;
    }
    for(auto it=paper_entity_tabu[op].begin();it!=paper_entity_tabu[op].end();) {
        if(it->second<=iter) it=paper_entity_tabu[op].erase(it);else ++it;
    }
    FleetTrips fleet(h+d);
    vector<vi> original(h+d);
    for(int v=0;v<h+d;++v) {
        const auto& route=v<h?initial.truck_routes[v]:initial.drone_routes[v-h];
        fleet[v]=split_trips(route);original[v]=join_trips(fleet[v]);
    }
    FleetTrips working=fleet; // one fleet copy, reused by every move
    // C is distinct from source/middle vehicles, so its original trips do not
    // depend on the chosen u/A/B. Reuse each (displaced customer, C) shortlist.
    map<pair<int,int>,vector<N7Insertion>> n7_insertion_cache;
    // Enumerate ALL eligible ordered triples of distinct vehicles.
    // Shuffling changes enumeration order only; no vehicle-triplet cap.
    const int vehicle_count=h+d;
    vector<vector<vi>> n7_destinations;
    vi n7_source_order;
    if(op==7) {
        n7_destinations.assign(vehicle_count,vector<vi>(vehicle_count));
        mt19937 rng(static_cast<unsigned>(search_rng()));
        vi source_order(vehicle_count);iota(source_order.begin(),source_order.end(),0);
        shuffle(source_order.begin(),source_order.end(),rng);
        n7_source_order=source_order;
        vector<array<int,3>> triples;
        for(int i=0;i<vehicle_count;++i) {
            if(fleet[i].empty()) continue;
            for(int j=0;j<vehicle_count;++j) {
                if(i==j||fleet[j].empty()) continue;
                for(int k=0;k<vehicle_count;++k) {
                    if(k==i||k==j) continue;
                    triples.push_back({i,j,k});
                }
            }
        }
        shuffle(triples.begin(),triples.end(),rng);
        for(int index=0;index<(int)triples.size();++index) {
            const auto& selected=triples[index];
            n7_destinations[selected[0]][selected[1]].push_back(selected[2]);
        }
    }
    vector<int> sources;
    if(fleet_scope||op>=7) {sources.resize(h+d);iota(sources.begin(),sources.end(),0);}
    else sources=critical_vehicle_indices_by_makespan(initial);
    if(op==7) sources=n7_source_order;
    vector<char> source_allowed(h+d,0);
    for(int v:sources) source_allowed[v]=1;
    auto reversed_source_already_visited=[&](int a,int r,int b,int s) {
        return source_allowed[b] && make_pair(b,s)<make_pair(a,r);
    };
    Solution best=initial,candidate=initial;
    double best_score=numeric_limits<double>::infinity();int ties=0;
    SolutionEvaluationContext context(initial);
    vector<vi> scratch(h+d);
    vector<ChangedVehicle> changes;changes.reserve(3);
    vi changed_ids;changed_ids.reserve(3);
    // At most two moved customer/block attributes, set by the move generator.
    // Fixed storage avoids allocating keys for each evaluated candidate.
    array<MoveEntity,2> move_entities{};
    int move_entity_count=0;
    vector<unordered_set<long long>> old_edges(h+d);
    auto edge_code=[](int from,int to){return (long long)from*(n+1)+to;};
    if(!paper_tabu[op].empty()) for(int v=0;v<h+d;++v)
        for(int p=1;p<(int)original[v].size();++p)
            if(original[v][p-1]!=original[v][p]) old_edges[v].insert(edge_code(original[v][p-1],original[v][p]));

    auto consider=[&](initializer_list<int> affected) {
        check_search_time_limit();changes.clear();changed_ids.clear();
        // Swap flat route buffers in and back; no full Solution/Fleet copy.
        struct Restore {
            Solution& candidate;vector<vi>& scratch;vi& ids;
            ~Restore(){for(int v:ids) (v<h?candidate.truck_routes[v]:candidate.drone_routes[v-h]).swap(scratch[v]);}
        } restore{candidate,scratch,changed_ids};
        array<int,3> seen{};int count=0;
        for(int v:affected) {
            bool duplicate=false;for(int p=0;p<count;++p) if(seen[p]==v) duplicate=true;
            if(duplicate) continue;
            seen[count++]=v;
            flatten_trips_into(working[v],scratch[v]);
            if(scratch[v]==original[v]) continue;
            if(v>=h) for(int customer:scratch[v]) if(customer&&!served_by_drone[customer]) return;
            int first=0,shared=min(scratch[v].size(),original[v].size());
            while(first<shared&&scratch[v][first]==original[v][first]) ++first;
            (v<h?candidate.truck_routes[v]:candidate.drone_routes[v-h]).swap(scratch[v]);
            changed_ids.push_back(v);
            changes.push_back({v<h,v<h?v:v-h,first});
        }
        if(changes.empty()) return;
        const double value = context.score_candidate(candidate, changes, cost);
        // Every complete feasible candidate is observed before any filtering.
        if(value>best_score+1e-8) return;
        if(!(value+1e-8<reference)) {
            for(int i=0;i<move_entity_count;++i) {
                auto it=paper_entity_tabu[op].find(move_entities[i]);
                if(it!=paper_entity_tabu[op].end()&&it->second>iter) return;
            }
        }
        if(!paper_tabu[op].empty() && !(value+1e-8<reference)) {
            for(int v:changed_ids) {
                const vi& route=v<h?candidate.truck_routes[v]:candidate.drone_routes[v-h];
                for(int p=1;p<(int)route.size();++p) {
                    int from=route[p-1],to=route[p];
                    if(from==to||old_edges[v].count(edge_code(from,to))) continue;
                    auto it=paper_tabu[op].find({v,from,to});
                    if(it!=paper_tabu[op].end()&&it->second>iter) return;
                }
            }
        }
        if(better_score_with_random_tie(value,best_score,ties)) {
            best=candidate;best_score=value;paper_candidate_found=true;
            pending_move_entities.assign(move_entities.begin(),move_entities.begin()+move_entity_count);
        }
    };

    for(int a:sources) for(int r=0;r<(int)fleet[a].size();++r) {
        check_search_time_limit();const vi& first=fleet[a][r];
        if(op==0||op==2) {
            int length=op==0?1:2;
            for(int p=0;p+length<=(int)first.size();++p) {
                vi block(first.begin()+p,first.begin()+p+length);
                move_entities[0]=tabu_block_attribute(block);move_entity_count=1;
                working[a][r].erase(working[a][r].begin()+p,working[a][r].begin()+p+length);
                for(int b=0;b<h+d;++b) {
                    if(b>=h&&any_of(block.begin(),block.end(),[](int c){return !served_by_drone[c];})) continue;
                    visit_insertions_inplace(working[b],block,[&](bool new_trip,int s,int position) {
                        // Adjacent equal-size block exchange has two relocate
                        // descriptions. Retain the one moving the left block right.
                        if(!new_trip&&a==b&&r==s&&position==p-length) return;
                        consider({a,b});
                    });
                }
                working[a][r].insert(working[a][r].begin()+p,block.begin(),block.end());
            }
        } else if(op==3) {
            for(int p=0;p<(int)first.size();++p) for(int q=p+1;q<(int)first.size();++q) {
                reverse(working[a][r].begin()+p,working[a][r].begin()+q+1);
                consider({a});
                reverse(working[a][r].begin()+p,working[a][r].begin()+q+1);
            }
        } else if(op==1||op==5||op==6) {
            vector<pair<int,int>> sizes=op==1?vector<pair<int,int>>{{1,1}}:
                op==5?vector<pair<int,int>>{{2,1},{1,2}}:vector<pair<int,int>>{{2,2}};
            for(auto lengths:sizes) for(int b=0;b<h+d;++b) for(int s=0;s<(int)fleet[b].size();++s) {
                bool same=a==b&&r==s;
                if(op==5&&same) continue;
                if(!same&&reversed_source_already_visited(a,r,b,s)) continue;
                const vi& second=fleet[b][s];int la=lengths.first,lb=lengths.second;
                for(int p=0;p+la<=(int)first.size();++p) for(int q=0;q+lb<=(int)second.size();++q) {
                    if(same&&p+la>q) continue; // disjoint blocks, one orientation
                    vi x(first.begin()+p,first.begin()+p+la),y(second.begin()+q,second.begin()+q+lb);
                    move_entities[0]=tabu_block_attribute(x);
                    move_entities[1]=tabu_block_attribute(y);move_entity_count=2;
                    if((a>=h&&any_of(y.begin(),y.end(),[](int c){return !served_by_drone[c];}))||
                       (b>=h&&any_of(x.begin(),x.end(),[](int c){return !served_by_drone[c];}))) continue;
                    if(same) {
                        swap_ranges(working[a][r].begin()+p,working[a][r].begin()+p+la,working[a][r].begin()+q);
                        consider({a});
                        swap_ranges(working[a][r].begin()+p,working[a][r].begin()+p+la,working[a][r].begin()+q);
                    } else {
                        auto replace=[](vi& route,int pos,int length,const vi& block) {
                            route.erase(route.begin()+pos,route.begin()+pos+length);
                            route.insert(route.begin()+pos,block.begin(),block.end());
                        };
                        replace(working[a][r],p,la,y);replace(working[b][s],q,lb,x);
                        consider({a,b});
                        replace(working[a][r],p,lb,x);replace(working[b][s],q,la,y);
                    }
                }
            }
        } else if(op==4) {
            for(int b=0;b<h+d;++b) for(int s=0;s<(int)fleet[b].size();++s) {
                if(a==b&&r==s) continue;
                if(reversed_source_already_visited(a,r,b,s)) continue;
                const vi& second=fleet[b][s];
                for(int p=0;p<=(int)first.size();++p) for(int q=0;q<=(int)second.size();++q) {
                    if(p==(int)first.size()&&q==(int)second.size()) continue;
                    vi u(first.begin(),first.begin()+p),v(second.begin(),second.begin()+q);
                    u.insert(u.end(),second.begin()+q,second.end());v.insert(v.end(),first.begin()+p,first.end());
                    working[a][r].swap(u);working[b][s].swap(v);
                    consider({a,b});
                    working[a][r].swap(u);working[b][s].swap(v);
                }
            }
        } else if(op==7) {
            // Experimental depth-2 chain: moved replaces displaced at q.
            // Explore every eligible vehicle triple. Within each
            // selected triple retain every source/middle customer and trip;
            // score only the top three insertions across the destination C.
            for(int p=0;p<(int)first.size();++p) {
                int moved=first[p];working[a][r].erase(working[a][r].begin()+p);
                for(int b=0;b<h+d;++b) for(int s=0;s<(int)fleet[b].size();++s) {
                    if(n7_destinations[a][b].empty()) continue;
                    if(a==b&&r==s) continue;
                    if(b>=h&&!served_by_drone[moved]) continue;
                    for(int q=0;q<(int)fleet[b][s].size();++q) {
                        int displaced=fleet[b][s][q];
                        move_entities[0]={TabuEntityKind::Customer,moved,0};
                        move_entities[1]={TabuEntityKind::Customer,displaced,0};move_entity_count=2;
                        working[b][s].erase(working[b][s].begin()+q);
                        vi inserted{displaced};
                        for(int insert=q;insert<=q;++insert) {
                            working[b][s].insert(working[b][s].begin()+insert,moved);
                            for(int c:n7_destinations[a][b]) {
                                if(c>=h&&!served_by_drone[displaced]) continue;
                                auto key=make_pair(displaced,c);
                                auto found=n7_insertion_cache.find(key);
                                if(found==n7_insertion_cache.end())
                                    found=n7_insertion_cache.emplace(key,n7_top3_insertions(fleet[c],displaced,c<h)).first;
                                for(const auto& insertion:found->second) {
                                    int t=insertion.trip,k=insertion.position;
                                    if(!insertion.new_trip) {
                                        working[c][t].insert(working[c][t].begin()+k,displaced);
                                        consider({a,b,c});
                                        working[c][t].erase(working[c][t].begin()+k);
                                    } else {
                                        working[c].insert(working[c].begin()+t,std::move(inserted));
                                        consider({a,b,c});
                                        inserted=std::move(working[c][t]);working[c].erase(working[c].begin()+t);
                                    }
                                }
                            }
                            working[b][s].erase(working[b][s].begin()+insert);
                        }
                        working[b][s].insert(working[b][s].begin()+q,displaced);
                    }
                }
                working[a][r].insert(working[a][r].begin()+p,moved);
            }
        } else if(op==8) {
            for(int s=r+1;s<(int)fleet[a].size();++s)
                for(int order=0;order<2;++order)
                    for(int reverse_a=0;reverse_a<(first.size()>1?2:1);++reverse_a)
                        for(int reverse_b=0;reverse_b<(fleet[a][s].size()>1?2:1);++reverse_b) {
                            vi x=first,y=fleet[a][s];
                            if(reverse_a) reverse(x.begin(),x.end());
                            if(reverse_b) reverse(y.begin(),y.end());
                            if(order) swap(x,y);
                            x.insert(x.end(),y.begin(),y.end());
                            vi old_a,old_b;old_a.swap(working[a][r]);old_b.swap(working[a][s]);
                            for(int position:{r,s}) {
                                if(position==s&&s==r+1) continue; // adjacent placements are identical
                                working[a][position].swap(x);consider({a});working[a][position].swap(x);
                            }
                            old_a.swap(working[a][r]);old_b.swap(working[a][s]);
                        }
        }
    }
    #ifndef NDEBUG
    if(working!=fleet) throw logic_error("A neighbourhood move did not restore the base trips");
    #endif
    // Derive reverse-move attributes just once for the selected neighbour.
    if(paper_candidate_found) {
        for(int v=0;v<h+d;++v) {
            const vi& route=v<h?best.truck_routes[v]:best.drone_routes[v-h];
            if(route==original[v]) continue;
            auto before=vehicle_edges(v,original[v]),after=vehicle_edges(v,route);
            set_difference(before.begin(),before.end(),after.begin(),after.end(),back_inserter(pending_removed_edges));
        }
        pending_operator=op;
    }
    return best;
}

Solution local_search(const Solution& sol,int op,int iter,double reference,double (*cost)(const Solution&)) {
    return paper_local_search(sol,op,iter,reference,cost,false);
}
Solution local_search_all_vehicle(const Solution& sol,int op,int iter,double reference,double (*cost)(const Solution&)) {
    return paper_local_search(sol,op,iter,reference,cost,true);
}
